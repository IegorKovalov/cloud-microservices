// SPDX-License-Identifier: MIT
//
// cpp-worker: high-throughput compute service backing the
// `square_sum` operation in the cloud-microservices framework.
//
// Demonstrates two patterns required by the project rules:
//   * a thread pool (see thread_pool.hpp) processing work items
//     concurrently,
//   * a std::mutex-protected shared accumulator: each worker computes
//     a partial sum-of-squares and merges it into a single shared
//     double under a lock.
//
// Exposes three HTTP endpoints over cpp-httplib:
//   GET  /health      -> liveness probe (compatible with shared.models.HealthStatus)
//   GET  /metrics     -> request_count / error_count / avg_latency_ms / uptime_seconds
//   POST /square_sum  -> body {"items": [...numbers...]} -> {"result": ..., ...}

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <ctime>
#include <exception>
#include <functional>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include <httplib.h>
#include <nlohmann/json.hpp>

#include "thread_pool.hpp"

namespace {

using json = nlohmann::json;
using cpp_worker::ThreadPool;

constexpr const char* kServiceName = "cpp-worker";

/// @brief Process metrics shared across all HTTP handlers.
struct Metrics {
  std::atomic<std::uint64_t> request_count{0};
  std::atomic<std::uint64_t> error_count{0};
  mutable std::mutex latency_mutex;
  double total_latency_ms{0.0};
  std::chrono::steady_clock::time_point started_at =
      std::chrono::steady_clock::now();

  /// @brief Record a single completed request.
  void Record(double duration_ms, bool is_error) {
    request_count.fetch_add(1, std::memory_order_relaxed);
    if (is_error) {
      error_count.fetch_add(1, std::memory_order_relaxed);
    }
    std::lock_guard<std::mutex> lock(latency_mutex);
    total_latency_ms += duration_ms;
  }

  /// @brief Snapshot the current counters as a JSON document.
  json Snapshot() const {
    const auto rc = request_count.load(std::memory_order_relaxed);
    const auto ec = error_count.load(std::memory_order_relaxed);
    double total = 0.0;
    {
      std::lock_guard<std::mutex> lock(latency_mutex);
      total = total_latency_ms;
    }
    const double avg = rc > 0 ? total / static_cast<double>(rc) : 0.0;
    const auto now = std::chrono::steady_clock::now();
    const double uptime =
        std::chrono::duration<double>(now - started_at).count();
    return json{
        {"service", kServiceName},
        {"request_count", rc},
        {"error_count", ec},
        {"avg_latency_ms", avg},
        {"uptime_seconds", uptime},
    };
  }
};

/// @brief Format the current UTC time as an ISO-8601 string with millis.
std::string IsoNowUtc() {
  using namespace std::chrono;
  const auto now = system_clock::now();
  const auto t = system_clock::to_time_t(now);
  const auto ms =
      duration_cast<milliseconds>(now.time_since_epoch()) % 1000;
  std::tm tm{};
  gmtime_r(&t, &tm);
  std::ostringstream oss;
  oss << std::put_time(&tm, "%Y-%m-%dT%H:%M:%S") << '.'
      << std::setfill('0') << std::setw(3) << ms.count() << "Z";
  return oss.str();
}

/// @brief Emit one structured-log JSON line on stdout.
void LogJson(const std::string& level, const std::string& message,
             const json& extra = json::object()) {
  json line{
      {"timestamp", IsoNowUtc()},
      {"level", level},
      {"service", kServiceName},
      {"message", message},
  };
  for (auto it = extra.begin(); it != extra.end(); ++it) {
    line[it.key()] = it.value();
  }
  std::cout << line.dump() << std::endl;
}

/// @brief Compute sum(items[i]^2) using @p pool with a mutex-guarded accumulator.
/// @param items Numbers to square and sum.
/// @param pool Thread pool used to parallelise the work.
/// @return Total sum of squares.
double ComputeSquareSumThreaded(const std::vector<double>& items,
                                ThreadPool& pool) {
  if (items.empty()) {
    return 0.0;
  }
  const std::size_t workers = std::max<std::size_t>(1, pool.Size());
  const std::size_t chunk = (items.size() + workers - 1) / workers;
  std::mutex acc_mutex;
  double accumulator = 0.0;
  std::vector<std::future<void>> futures;
  futures.reserve(workers);
  for (std::size_t w = 0; w < workers; ++w) {
    const std::size_t start = w * chunk;
    if (start >= items.size()) {
      break;
    }
    const std::size_t end = std::min(start + chunk, items.size());
    futures.emplace_back(pool.Submit([&items, &acc_mutex, &accumulator, start, end] {
      double partial = 0.0;
      for (std::size_t i = start; i < end; ++i) {
        partial += items[i] * items[i];
      }
      std::lock_guard<std::mutex> lock(acc_mutex);
      accumulator += partial;
    }));
  }
  for (auto& f : futures) {
    f.get();
  }
  return accumulator;
}

/// @brief Read the listen port from $SERVICE_PORT, defaulting to 8003.
int ReadPort() {
  const char* raw = std::getenv("SERVICE_PORT");
  if (raw == nullptr || *raw == '\0') {
    return 8003;
  }
  try {
    return std::stoi(raw);
  } catch (const std::exception&) {
    return 8003;
  }
}

/// @brief Read the worker count from $CPP_WORKER_THREADS, defaulting sensibly.
std::size_t ReadWorkerCount() {
  const char* raw = std::getenv("CPP_WORKER_THREADS");
  if (raw == nullptr || *raw == '\0') {
    const auto hw = std::thread::hardware_concurrency();
    return hw > 0 ? static_cast<std::size_t>(hw) : 4U;
  }
  try {
    const auto n = std::stoul(raw);
    return n == 0 ? 2U : static_cast<std::size_t>(n);
  } catch (const std::exception&) {
    return 2U;
  }
}

using Handler = std::function<void(const httplib::Request&, httplib::Response&)>;

/// @brief Wrap a raw handler with timing + metrics + structured logs.
Handler InstrumentHandler(Metrics& metrics, Handler handler) {
  return [&metrics, handler = std::move(handler)](
             const httplib::Request& req, httplib::Response& res) {
    const auto start = std::chrono::steady_clock::now();
    bool is_error = false;
    try {
      handler(req, res);
      if (res.status >= 500) {
        is_error = true;
      }
    } catch (const std::exception& e) {
      is_error = true;
      res.status = 500;
      res.set_content(json{{"detail", e.what()}}.dump(), "application/json");
    }
    const auto end = std::chrono::steady_clock::now();
    const double duration_ms =
        std::chrono::duration<double, std::milli>(end - start).count();
    metrics.Record(duration_ms, is_error);
    LogJson("info", "request_completed",
            json{{"path", req.path},
                 {"method", req.method},
                 {"status_code", res.status},
                 {"duration_ms", duration_ms},
                 {"is_error", is_error}});
  };
}

}  // namespace

int main() {
  const int port = ReadPort();
  const std::size_t worker_count = ReadWorkerCount();
  ThreadPool pool(worker_count);
  Metrics metrics;

  httplib::Server server;

  server.Get("/health", InstrumentHandler(metrics,
      [](const httplib::Request&, httplib::Response& res) {
        const json body{
            {"status", "ok"},
            {"service", kServiceName},
            {"timestamp", IsoNowUtc()},
        };
        res.set_content(body.dump(), "application/json");
      }));

  server.Get("/metrics", InstrumentHandler(metrics,
      [&metrics](const httplib::Request&, httplib::Response& res) {
        res.set_content(metrics.Snapshot().dump(), "application/json");
      }));

  server.Post("/square_sum", InstrumentHandler(metrics,
      [&pool](const httplib::Request& req, httplib::Response& res) {
        json body;
        try {
          body = json::parse(req.body);
        } catch (const std::exception&) {
          res.status = 400;
          res.set_content(json{{"detail", "invalid json"}}.dump(),
                          "application/json");
          return;
        }
        if (!body.contains("items") || !body["items"].is_array()) {
          res.status = 400;
          res.set_content(
              json{{"detail", "missing 'items' array"}}.dump(),
              "application/json");
          return;
        }
        std::vector<double> items;
        items.reserve(body["items"].size());
        for (const auto& v : body["items"]) {
          if (!v.is_number()) {
            res.status = 400;
            res.set_content(
                json{{"detail", "items must be numbers"}}.dump(),
                "application/json");
            return;
          }
          items.push_back(v.get<double>());
        }
        const auto t0 = std::chrono::steady_clock::now();
        const double result = ComputeSquareSumThreaded(items, pool);
        const auto t1 = std::chrono::steady_clock::now();
        const double duration_ms =
            std::chrono::duration<double, std::milli>(t1 - t0).count();
        const json out{
            {"service", kServiceName},
            {"items_processed", items.size()},
            {"result", result},
            {"workers", pool.Size()},
            {"duration_ms", duration_ms},
        };
        res.set_content(out.dump(), "application/json");
      }));

  LogJson("info", "starting",
          json{{"port", port}, {"workers", worker_count}});

  if (!server.listen("0.0.0.0", port)) {
    LogJson("error", "listen_failed", json{{"port", port}});
    return 1;
  }
  return 0;
}

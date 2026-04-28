// SPDX-License-Identifier: MIT
//
// Minimal C++17 thread pool used by cpp-worker.
//
// Demonstrates the pattern explicitly required by the project rules:
//   * a fixed pool of std::thread workers,
//   * a std::mutex + std::condition_variable guarded task queue,
//   * std::packaged_task for typed return values via std::future.
//
// RAII: workers are joined in the destructor; submitting after
// destruction begins is undefined and not allowed.

#pragma once

#include <atomic>
#include <condition_variable>
#include <cstddef>
#include <functional>
#include <future>
#include <memory>
#include <mutex>
#include <queue>
#include <stdexcept>
#include <thread>
#include <type_traits>
#include <utility>
#include <vector>

namespace cpp_worker {

/// @brief Fixed-size pool of worker threads consuming a shared task queue.
class ThreadPool {
 public:
  /// @brief Construct a pool with @p worker_count active threads.
  /// @param worker_count Number of OS threads to spawn. Must be > 0.
  /// @throws std::invalid_argument if @p worker_count is 0.
  explicit ThreadPool(std::size_t worker_count) {
    if (worker_count == 0) {
      throw std::invalid_argument("ThreadPool: worker_count must be > 0");
    }
    workers_.reserve(worker_count);
    for (std::size_t i = 0; i < worker_count; ++i) {
      workers_.emplace_back([this] { Loop(); });
    }
  }

  ThreadPool(const ThreadPool&) = delete;
  ThreadPool& operator=(const ThreadPool&) = delete;
  ThreadPool(ThreadPool&&) = delete;
  ThreadPool& operator=(ThreadPool&&) = delete;

  /// @brief Stop all workers and join them.
  ~ThreadPool() {
    {
      std::lock_guard<std::mutex> lock(mutex_);
      stop_ = true;
    }
    cv_.notify_all();
    for (auto& worker : workers_) {
      if (worker.joinable()) {
        worker.join();
      }
    }
  }

  /// @brief Submit a callable to be executed by some worker thread.
  /// @tparam F Any nullary callable.
  /// @param fn Callable to execute.
  /// @return std::future yielding the callable's return value.
  template <typename F>
  auto Submit(F&& fn) -> std::future<typename std::invoke_result_t<F>> {
    using R = typename std::invoke_result_t<F>;
    auto task = std::make_shared<std::packaged_task<R()>>(std::forward<F>(fn));
    std::future<R> future = task->get_future();
    {
      std::lock_guard<std::mutex> lock(mutex_);
      if (stop_) {
        throw std::runtime_error("ThreadPool: submit after stop");
      }
      tasks_.emplace([task] { (*task)(); });
    }
    cv_.notify_one();
    return future;
  }

  /// @brief Number of worker threads owned by this pool.
  /// @return Worker thread count.
  std::size_t Size() const noexcept { return workers_.size(); }

 private:
  void Loop() {
    for (;;) {
      std::function<void()> job;
      {
        std::unique_lock<std::mutex> lock(mutex_);
        cv_.wait(lock, [this] { return stop_ || !tasks_.empty(); });
        if (stop_ && tasks_.empty()) {
          return;
        }
        job = std::move(tasks_.front());
        tasks_.pop();
      }
      job();
    }
  }

  std::vector<std::thread> workers_;
  std::queue<std::function<void()>> tasks_;
  mutable std::mutex mutex_;
  std::condition_variable cv_;
  bool stop_ = false;
};

}  // namespace cpp_worker

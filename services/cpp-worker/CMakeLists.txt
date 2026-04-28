# CMake build for the cpp-worker service.
#
# Pulls in cpp-httplib (single-header HTTP server) and nlohmann_json
# via FetchContent so the source tree stays minimal. Compiles with the
# strict warning set required by the project rules.

cmake_minimum_required(VERSION 3.20)
project(cpp_worker LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Always emit compile_commands.json so clangd (used by Cursor / VS Code)
# can resolve include paths for cpp-httplib and nlohmann/json.
set(CMAKE_EXPORT_COMPILE_COMMANDS ON CACHE INTERNAL "")

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

include(FetchContent)

FetchContent_Declare(
    httplib
    GIT_REPOSITORY https://github.com/yhirose/cpp-httplib.git
    GIT_TAG v0.18.1
    GIT_SHALLOW TRUE
)
set(HTTPLIB_COMPILE OFF CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(httplib)

FetchContent_Declare(
    nlohmann_json
    GIT_REPOSITORY https://github.com/nlohmann/json.git
    GIT_TAG v3.11.3
    GIT_SHALLOW TRUE
)
set(JSON_BuildTests OFF CACHE INTERNAL "")
FetchContent_MakeAvailable(nlohmann_json)

find_package(Threads REQUIRED)

add_executable(cpp_worker
    src/main.cpp
)

target_include_directories(cpp_worker PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src
)

target_compile_options(cpp_worker PRIVATE
    -Wall -Wextra -Werror -O2
)

target_link_libraries(cpp_worker PRIVATE
    httplib::httplib
    nlohmann_json::nlohmann_json
    Threads::Threads
)

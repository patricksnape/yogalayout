cmake_minimum_required(VERSION 3.15...3.30)
project(${SKBUILD_PROJECT_NAME} LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

find_package(Python 3.10 COMPONENTS Interpreter Development.Module
             ${SKBUILD_SABI_COMPONENT} REQUIRED)
execute_process(COMMAND "${Python_EXECUTABLE}" -m nanobind --cmake_dir
                OUTPUT_STRIP_TRAILING_WHITESPACE OUTPUT_VARIABLE nanobind_ROOT)
find_package(nanobind CONFIG REQUIRED)

# Yoga is a pinned git submodule; only the library itself is built (no tests/install rules).
if(NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/extern/yoga/yoga/CMakeLists.txt")
  message(FATAL_ERROR "extern/yoga is empty: run `git submodule update --init`")
endif()
add_subdirectory(extern/yoga/yoga EXCLUDE_FROM_ALL)

nanobind_add_module(_core STABLE_ABI NB_STATIC src/yogalayout/_core.cpp)
target_link_libraries(_core PRIVATE yogacore)
target_include_directories(_core PRIVATE extern/yoga)
install(TARGETS _core LIBRARY DESTINATION yogalayout)

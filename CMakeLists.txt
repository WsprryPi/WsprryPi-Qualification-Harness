cmake_minimum_required(VERSION 3.21)
project(wsprrypi_capture VERSION 0.2.0 LANGUAGES CXX)

option(WSPQ_BUILD_SOAPY "Build the physical SoapySDR adapter (never run by tests)" OFF)
option(WSPQ_BUILD_TESTS "Build hardware-free native tests" ON)

add_library(wspq_capture native/src/capture.cpp native/src/sha256.cpp)
target_include_directories(wspq_capture PUBLIC native/include)
target_compile_features(wspq_capture PUBLIC cxx_std_17)

function(wspq_enable_warnings target)
  if(MSVC)
    target_compile_options(${target} PRIVATE /W4 /permissive-)
  else()
    target_compile_options(${target} PRIVATE -Wall -Wextra -Wpedantic -Wconversion)
  endif()
endfunction()
wspq_enable_warnings(wspq_capture)

add_executable(wspq-capture-mock native/src/mock_capture.cpp)
target_link_libraries(wspq-capture-mock PRIVATE wspq_capture)
wspq_enable_warnings(wspq-capture-mock)

if(WSPQ_BUILD_SOAPY)
  find_package(SoapySDR CONFIG REQUIRED)
  add_executable(wspq-capture-soapy native/src/soapy_capture.cpp)
  target_include_directories(wspq-capture-soapy PRIVATE ${SoapySDR_INCLUDE_DIRS})
  target_link_libraries(wspq-capture-soapy PRIVATE wspq_capture ${SoapySDR_LIBRARIES})
  wspq_enable_warnings(wspq-capture-soapy)
endif()

if(WSPQ_BUILD_TESTS)
  include(CTest)
  add_executable(wspq-capture-tests native/tests/capture_tests.cpp)
  target_link_libraries(wspq-capture-tests PRIVATE wspq_capture)
  wspq_enable_warnings(wspq-capture-tests)
  add_test(NAME capture-contract COMMAND wspq-capture-tests)
  add_test(
    NAME mock-cli-contract
    COMMAND ${CMAKE_COMMAND}
      -DMOCK_EXECUTABLE=$<TARGET_FILE:wspq-capture-mock>
      -DTEST_ROOT=${CMAKE_CURRENT_BINARY_DIR}/mock_cli_path_with_spaces
      -P ${CMAKE_CURRENT_SOURCE_DIR}/native/tests/mock_cli_test.cmake
  )
endif()

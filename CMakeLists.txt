cmake_minimum_required(VERSION 3.21)
project(wsprrypi_capture VERSION 0.2.0 LANGUAGES CXX)

option(WSPQ_BUILD_SOAPY "Build the physical SoapySDR adapter (never run by tests)" OFF)
option(WSPQ_BUILD_TESTS "Build hardware-free native tests" ON)
set(WSPQ_PICO_SOURCE "" CACHE PATH "Explicit candidate Pico checkout for the production job compiler")

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

if(WSPQ_PICO_SOURCE)
  find_package(Git REQUIRED)
  execute_process(COMMAND ${GIT_EXECUTABLE} -C "${WSPQ_PICO_SOURCE}" rev-parse HEAD
    OUTPUT_VARIABLE WSPQ_PICO_REVISION OUTPUT_STRIP_TRAILING_WHITESPACE
    COMMAND_ERROR_IS_FATAL ANY)
  file(GLOB_RECURSE WSPQ_PICO_ENCODER_FILES CONFIGURE_DEPENDS
    "${WSPQ_PICO_SOURCE}/src/encoding/*.cpp" "${WSPQ_PICO_SOURCE}/src/encoding/*.hpp"
    "${WSPQ_PICO_SOURCE}/src/wtp/*.cpp" "${WSPQ_PICO_SOURCE}/src/wtp/*.hpp")
  set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${WSPQ_PICO_ENCODER_FILES})
  set(WSPQ_PICO_ENCODER_JSON "[")
  set(WSPQ_PICO_ENCODER_SEPARATOR "")
  foreach(WSPQ_PICO_ENCODER_FILE IN LISTS WSPQ_PICO_ENCODER_FILES)
    file(RELATIVE_PATH WSPQ_PICO_ENCODER_RELATIVE "${WSPQ_PICO_SOURCE}/src" "${WSPQ_PICO_ENCODER_FILE}")
    file(SHA256 "${WSPQ_PICO_ENCODER_FILE}" WSPQ_PICO_ENCODER_HASH)
    string(APPEND WSPQ_PICO_ENCODER_JSON
      "${WSPQ_PICO_ENCODER_SEPARATOR}{\"path\":\"${WSPQ_PICO_ENCODER_RELATIVE}\",\"sha256\":\"${WSPQ_PICO_ENCODER_HASH}\"}")
    set(WSPQ_PICO_ENCODER_SEPARATOR ",")
  endforeach()
  string(APPEND WSPQ_PICO_ENCODER_JSON "]")
  file(GENERATE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/pico-identity/pico_encoder_identity.hpp"
    CONTENT "#pragma once\ninline constexpr const char* wspq_pico_encoder_inputs = R\"WSPQ(${WSPQ_PICO_ENCODER_JSON})WSPQ\";\n")
  add_executable(wspq-pico-compile native/src/pico_compile.cpp
    "${WSPQ_PICO_SOURCE}/src/encoding/wspr.cpp"
    "${WSPQ_PICO_SOURCE}/src/encoding/wspr_plan.cpp"
    "${WSPQ_PICO_SOURCE}/src/wtp/json.cpp")
  target_include_directories(wspq-pico-compile PRIVATE "${WSPQ_PICO_SOURCE}/src"
    "${CMAKE_CURRENT_BINARY_DIR}/pico-identity")
  target_compile_features(wspq-pico-compile PRIVATE cxx_std_20)
  set_target_properties(wspq-pico-compile PROPERTIES
    CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
  target_compile_definitions(wspq-pico-compile PRIVATE WSPQ_PICO_REVISION="${WSPQ_PICO_REVISION}")
  wspq_enable_warnings(wspq-pico-compile)
  if(WSPQ_BUILD_TESTS)
    add_executable(wspq-pico-governance native/tests/pico_governance.cpp
      "${WSPQ_PICO_SOURCE}/src/wtp/json.cpp"
      "${WSPQ_PICO_SOURCE}/src/wtp/codec.cpp"
      "${WSPQ_PICO_SOURCE}/src/wtp/endpoint.cpp"
      "${WSPQ_PICO_SOURCE}/src/wtp/frame_parser.cpp"
      "${WSPQ_PICO_SOURCE}/src/wtp/job_service.cpp"
      "${WSPQ_PICO_SOURCE}/src/wtp/sha256.cpp"
      "${WSPQ_PICO_SOURCE}/src/wtp/inhibited_rf_engine.cpp")
    target_include_directories(wspq-pico-governance PRIVATE "${WSPQ_PICO_SOURCE}/src"
      "${CMAKE_CURRENT_BINARY_DIR}/pico-identity")
    target_compile_definitions(wspq-pico-governance PRIVATE
      WSPQ_PICO_REVISION="${WSPQ_PICO_REVISION}")
    target_compile_features(wspq-pico-governance PRIVATE cxx_std_20)
    wspq_enable_warnings(wspq-pico-governance)
  endif()
endif()

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

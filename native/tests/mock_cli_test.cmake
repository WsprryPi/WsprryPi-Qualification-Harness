file(REMOVE_RECURSE "${TEST_ROOT}")
file(MAKE_DIRECTORY "${TEST_ROOT}")

set(success_iq "${TEST_ROOT}/success capture.cf32")
set(success_json "${TEST_ROOT}/success capture.json")
execute_process(
  COMMAND "${MOCK_EXECUTABLE}" 25 "${success_iq}" "${success_json}" 20260811T120000Z-mock
  RESULT_VARIABLE success_result
)
if(NOT success_result EQUAL 0)
  message(FATAL_ERROR "mock success returned ${success_result}")
endif()
file(SIZE "${success_iq}" success_size)
if(NOT success_size EQUAL 200)
  message(FATAL_ERROR "mock success wrote ${success_size} bytes instead of 200")
endif()
file(READ "${success_json}" success_evidence)
if(NOT success_evidence MATCHES "capture_success")
  message(FATAL_ERROR "mock success evidence is missing its discriminator")
endif()

set(failure_iq "${TEST_ROOT}/failure capture.cf32")
set(failure_json "${TEST_ROOT}/failure capture.json")
set(failure_report "${failure_json}.failure.json")
execute_process(
  COMMAND "${MOCK_EXECUTABLE}" 25 "${failure_iq}" "${failure_json}"
          20260811T120000Z-mock short-read
  RESULT_VARIABLE failure_result
)
if(NOT failure_result EQUAL 1)
  message(FATAL_ERROR "mock short-read returned ${failure_result} instead of 1")
endif()
if(EXISTS "${failure_iq}" OR EXISTS "${failure_json}")
  message(FATAL_ERROR "mock short-read left success-shaped artifacts")
endif()
file(READ "${failure_report}" failure_evidence)
if(NOT failure_evidence MATCHES "capture_failure" OR NOT failure_evidence MATCHES "short_read")
  message(FATAL_ERROR "mock failure evidence lacks its discriminator or primary cause")
endif()

file(REMOVE_RECURSE "${TEST_ROOT}")

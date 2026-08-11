from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_mock_target_has_no_soapy_dependency_or_fallback() -> None:
    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    mock = (ROOT / "native" / "src" / "mock_capture.cpp").read_text(encoding="utf-8")
    engine = (ROOT / "native" / "src" / "capture.cpp").read_text(encoding="utf-8")
    assert (
        'option(WSPQ_BUILD_SOAPY "Build the physical SoapySDR adapter (never run by tests)" OFF)'
        in cmake
    )
    assert "target_link_libraries(wspq-capture-mock PRIVATE wspq_capture)" in cmake
    assert "SoapySDR" not in mock
    assert "SoapySDR" not in engine


def test_ci_forces_hardware_free_native_configuration() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "-DWSPQ_BUILD_SOAPY=OFF" in workflow
    assert "SoapySDRUtil" not in workflow
    assert "wspq-capture-soapy" not in workflow

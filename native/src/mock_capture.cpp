#include "wsprrypi_capture/capture.hpp"

#include <algorithm>
#include <complex>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>

namespace {

class MockClock final : public wspq::Clock {
public:
    double monotonic_seconds() override { const auto value = monotonic_; monotonic_ += 0.001; return value; }
    std::string utc_now() override {
        const auto value = utc_index_++;
        return "2026-08-11T12:00:" + std::string(value < 10 ? "0" : "") +
               std::to_string(value) + ".000Z";
    }
private:
    double monotonic_{0.0};
    unsigned utc_index_{0};
};

class MockSource final : public wspq::SampleSource {
public:
    explicit MockSource(std::string scenario) : scenario_(std::move(scenario)) {}
    wspq::Settings configure(const wspq::Settings& requested) override { return requested; }
    wspq::ReadResult read(std::complex<float>* output, std::size_t capacity) override {
        if (scenario_ == "short-read" && reads_++ != 0)
            return {wspq::ReadKind::end, 0};
        const auto count = std::min(capacity, chunk_);
        for (std::size_t index = 0; index < count; ++index)
            output[index] = {0.125F, -0.25F};
        return {wspq::ReadKind::samples, count};
    }
    wspq::CleanupReport cleanup() noexcept override {
        return {{"mock_release"}, {}};
    }
    std::size_t chunk_{7};
private:
    std::string scenario_;
    std::size_t reads_{0};
};

}  // namespace

int main(int argc, char** argv) {
    if (argc != 5 && argc != 6) {
        std::cerr << "usage: wspq-capture-mock SAMPLE_COUNT OUTPUT METADATA CAPTURE_ID "
                     "[success|short-read]\n";
        return wspq::invalid_arguments;
    }
    try {
        wspq::CaptureRequest request;
        request.sample_count = static_cast<std::size_t>(std::stoull(argv[1]));
        request.output_path = argv[2];
        request.metadata_path = argv[3];
        request.failure_metadata_path = std::string(argv[3]) + ".failure.json";
        request.capture_id = argv[4];
        request.chunk_samples = 11;
        MockClock clock;
        request.clock = &clock;
        const std::string scenario = argc == 6 ? argv[5] : "success";
        if (scenario != "success" && scenario != "short-read")
            throw std::invalid_argument("unsupported mock scenario");
        MockSource source(scenario);
        const auto result = wspq::capture_exact(source, request);
        if (result.exit_code != 0) {
            std::cerr << result.primary_failure_cause << '\n';
            if (!result.evidence_error.empty()) std::cerr << "evidence: " << result.evidence_error << '\n';
        }
        return result.exit_code;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return wspq::invalid_arguments;
    }
}

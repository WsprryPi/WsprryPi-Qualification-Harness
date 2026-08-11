#pragma once

#include <complex>
#include <cstddef>
#include <filesystem>
#include <string>
#include <vector>

namespace wspq {

inline constexpr std::size_t cf32_bytes_per_sample = 8;

struct Settings {
    std::string driver{"mock"};
    std::string serial{"MOCK-0001"};
    std::string format{"CF32"};
    double sample_rate_hz{250000.0};
    double bandwidth_hz{200000.0};
    double center_frequency_hz{10140200.0};
    double gain_db{10.0};
    int channel{0};
    bool agc{false};
    bool bias_tee{false};
};

enum class ReadKind { samples, timeout, overflow, end, cancelled, error };

struct ReadResult {
    ReadKind kind{ReadKind::error};
    std::size_t count{0};
};

struct CleanupReport {
    std::vector<std::string> attempted_steps;
    std::vector<std::string> failed_steps;
};

class CleanupActions {
public:
    virtual ~CleanupActions() = default;
    virtual bool needs_deactivate() const noexcept = 0;
    virtual bool needs_close() const noexcept = 0;
    virtual bool needs_release() const noexcept = 0;
    virtual void deactivate() = 0;
    virtual void close() = 0;
    virtual void release() = 0;
};

CleanupReport run_cleanup_steps(CleanupActions& actions) noexcept;

class SampleSource {
public:
    virtual ~SampleSource() = default;
    virtual Settings configure(const Settings& requested) = 0;
    virtual ReadResult read(std::complex<float>* destination, std::size_t capacity) = 0;
    virtual CleanupReport cleanup() noexcept = 0;
};

class Clock {
public:
    virtual ~Clock() = default;
    virtual double monotonic_seconds() = 0;
    virtual std::string utc_now() = 0;
};

class SystemClock final : public Clock {
public:
    double monotonic_seconds() override;
    std::string utc_now() override;
};

struct CaptureTimestamps {
    std::string helper_start;
    std::string configuration_start;
    std::string configuration_complete;
    std::string first_read_start;
    std::string first_read_complete;
    std::string retained_capture_start;
    std::string retained_capture_complete;
    std::string cleanup_start;
    std::string cleanup_complete;
    std::string helper_complete;
};

struct CaptureRequest {
    std::string capture_id;
    std::filesystem::path output_path;
    std::filesystem::path metadata_path;
    std::filesystem::path failure_metadata_path;
    Settings requested;
    std::size_t sample_count{0};
    std::size_t chunk_samples{65536};
    unsigned max_timeouts{3};
    unsigned max_overflows{0};
    std::size_t max_read_calls{1000000};
    double max_elapsed_duration_s{600.0};
    long read_timeout_us{2000000L};
    float clipping_threshold{0.999F};
    Clock* clock{nullptr};
};

struct CaptureResult {
    int exit_code{1};
    std::string primary_outcome{"failed"};
    std::string primary_failure_cause;
    std::vector<std::string> failure_causes;
    std::string cleanup_outcome{"verified"};
    CleanupReport cleanup;
    Settings actual;
    bool actual_available{false};
    bool first_read_attempted{false};
    std::size_t retained_samples{0};
    std::size_t discarded_samples{0};
    std::size_t read_calls{0};
    std::size_t partial_reads{0};
    unsigned timeout_count{0};
    unsigned overflow_count{0};
    std::size_t clipped_samples{0};
    bool output_present{false};
    bool output_complete{false};
    std::size_t output_bytes{0};
    std::string output_sha256;
    std::size_t removed_incomplete_size_bytes{0};
    std::string removed_incomplete_sha256;
    double elapsed_duration_s{0.0};
    CaptureTimestamps timestamps;
    std::filesystem::path evidence_path;
    std::string evidence_error;
};

enum ExitCode {
    success = 0,
    capture_failed = 1,
    invalid_arguments = 2,
    overflow_rejected = 3,
    timed_out = 4,
    cancelled = 5,
    identity_mismatch = 6,
    settings_mismatch = 7,
    clipping_rejected = 8,
    cleanup_failed = 9,
    output_invalid = 10,
    evidence_failed = 11,
};

CaptureResult capture_exact(SampleSource& source, const CaptureRequest& request);
std::string sha256_file(const std::filesystem::path& path);

}  // namespace wspq

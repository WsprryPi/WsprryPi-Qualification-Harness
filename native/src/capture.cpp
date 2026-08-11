#include "wsprrypi_capture/capture.hpp"

#include <algorithm>
#include <chrono>
#include <climits>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <limits>
#include <sstream>
#include <stdexcept>

static_assert(CHAR_BIT == 8, "CF32 wire format requires eight-bit bytes");
static_assert(sizeof(float) == 4, "CF32 wire format requires 32-bit float");
static_assert(std::numeric_limits<float>::is_iec559,
              "CF32 wire format requires IEEE-754 binary32 float");

namespace wspq {
namespace {

bool close(double left, double right) {
    const double scale = std::max({1.0, std::abs(left), std::abs(right)});
    return std::abs(left - right) <= scale * 1e-9;
}

bool finite_settings(const Settings& settings) {
    return std::isfinite(settings.sample_rate_hz) && settings.sample_rate_hz > 0.0 &&
           std::isfinite(settings.bandwidth_hz) && settings.bandwidth_hz > 0.0 &&
           std::isfinite(settings.center_frequency_hz) && settings.center_frequency_hz > 0.0 &&
           std::isfinite(settings.gain_db) && settings.channel >= 0;
}

bool valid_utf8(const std::string& value) {
    std::size_t index = 0;
    while (index < value.size()) {
        const auto first = static_cast<unsigned char>(value[index]);
        std::size_t continuation = 0;
        std::uint32_t codepoint = 0;
        if (first <= 0x7fU) { ++index; continue; }
        if ((first & 0xe0U) == 0xc0U) { continuation=1; codepoint=first&0x1fU; }
        else if ((first & 0xf0U) == 0xe0U) { continuation=2; codepoint=first&0x0fU; }
        else if ((first & 0xf8U) == 0xf0U) { continuation=3; codepoint=first&0x07U; }
        else return false;
        if (index + continuation >= value.size()) return false;
        for (std::size_t offset=1; offset<=continuation; ++offset) {
            const auto next=static_cast<unsigned char>(value[index+offset]);
            if ((next&0xc0U)!=0x80U) return false;
            codepoint=(codepoint<<6U)|(next&0x3fU);
        }
        if ((continuation==1 && codepoint<0x80U) || (continuation==2 && codepoint<0x800U) ||
            (continuation==3 && codepoint<0x10000U) || codepoint>0x10ffffU ||
            (codepoint>=0xd800U && codepoint<=0xdfffU)) return false;
        index += continuation + 1;
    }
    return true;
}

std::string escape_json(const std::string& value) {
    std::ostringstream out;
    for (const char raw_character : value) {
        const auto character = static_cast<unsigned char>(raw_character);
        switch (character) {
        case '"': out << "\\\""; break;
        case '\\': out << "\\\\"; break;
        case '\n': out << "\\n"; break;
        case '\r': out << "\\r"; break;
        case '\t': out << "\\t"; break;
        default:
            if (character < 0x20U) {
                out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                    << static_cast<unsigned>(character) << std::dec;
            } else {
                out << static_cast<char>(character);
            }
        }
    }
    return out.str();
}

void string_or_null(std::ostream& output, const std::string& value) {
    if (value.empty()) output << "null";
    else output << '"' << escape_json(value) << '"';
}

void remove_if_present(const std::filesystem::path& path) noexcept {
    std::error_code error;
    std::filesystem::remove(path, error);
}

void append_unique(std::vector<std::string>& values, const std::string& value) {
    if (std::find(values.begin(), values.end(), value) == values.end()) values.push_back(value);
}

void write_settings(std::ostream& output, const Settings& settings) {
    output << std::boolalpha << std::setprecision(17)
           << "{\"format\": \"" << escape_json(settings.format)
           << "\", \"sample_rate_hz\": " << settings.sample_rate_hz
           << ", \"bandwidth_hz\": " << settings.bandwidth_hz
           << ", \"center_frequency_hz\": " << settings.center_frequency_hz
           << ", \"gain_db\": " << settings.gain_db << ", \"channel\": "
           << settings.channel << ", \"agc\": " << settings.agc
           << ", \"bias_tee\": " << settings.bias_tee << '}';
}

void write_string_array(std::ostream& output, const std::vector<std::string>& values) {
    output << '[';
    for (std::size_t index = 0; index < values.size(); ++index) {
        if (index != 0) output << ", ";
        output << '"' << escape_json(values[index]) << '"';
    }
    output << ']';
}

void write_evidence(const CaptureRequest& request, const CaptureResult& result,
                    const std::filesystem::path& destination) {
    const auto temporary = std::filesystem::path(destination.string() + ".incomplete");
    std::ofstream output(temporary, std::ios::binary | std::ios::trunc);
    if (!output) throw std::runtime_error("cannot create incomplete evidence");
    const bool succeeded = result.exit_code == success;
    output << std::boolalpha << std::setprecision(17)
           << "{\n  \"schema_version\": 1,\n  \"helper_version\": \"0.2.0\",\n"
           << "  \"evidence_type\": \"capture_" << (succeeded ? "success" : "failure")
           << "\",\n  \"capture_id\": \"" << escape_json(request.capture_id) << "\",\n"
           << "  \"timestamps\": {\n";
    const auto timestamp = [&](const char* name, const std::string& value, bool last = false) {
        output << "    \"" << name << "\": "; string_or_null(output, value);
        output << (last ? "\n" : ",\n");
    };
    timestamp("helper_start_utc", result.timestamps.helper_start);
    timestamp("configuration_start_utc", result.timestamps.configuration_start);
    timestamp("configuration_complete_utc", result.timestamps.configuration_complete);
    timestamp("first_read_start_utc", result.timestamps.first_read_start);
    timestamp("first_read_complete_utc", result.timestamps.first_read_complete);
    timestamp("retained_capture_start_utc", result.timestamps.retained_capture_start);
    timestamp("retained_capture_complete_utc", result.timestamps.retained_capture_complete);
    timestamp("cleanup_start_utc", result.timestamps.cleanup_start);
    timestamp("cleanup_complete_utc", result.timestamps.cleanup_complete);
    timestamp("helper_complete_utc", result.timestamps.helper_complete, true);
    output << "  },\n  \"elapsed_duration_s\": " << result.elapsed_duration_s
           << ",\n  \"limits\": {\"read_timeout_us\": " << request.read_timeout_us
           << ", \"max_elapsed_duration_s\": " << request.max_elapsed_duration_s
           << ", \"max_read_calls\": " << request.max_read_calls << "},\n"
           << "  \"requested_device\": {\"driver\": \"" << escape_json(request.requested.driver)
           << "\", \"serial\": \"" << escape_json(request.requested.serial) << "\"},\n"
           << "  \"resolved_device\": ";
    if (result.actual_available) {
        output << "{\"driver\": \"" << escape_json(result.actual.driver)
               << "\", \"serial\": \"" << escape_json(result.actual.serial) << "\"}";
    } else output << "null";
    output << ",\n  \"requested_settings\": "; write_settings(output, request.requested);
    output << ",\n  \"actual_settings\": ";
    if (result.actual_available) write_settings(output, result.actual); else output << "null";
    output << ",\n  \"wire_format\": {\"sample_format\": \"CF32\", "
              "\"component_type\": \"IEEE754_binary32\", \"interleave\": "
              "\"real_imaginary\", \"byte_order\": \"little_endian\", "
              "\"bytes_per_complex_sample\": 8},\n"
           << "  \"first_read\": {\"attempted\": " << result.first_read_attempted
           << ", \"discarded\": " << (result.discarded_samples != 0)
           << ", \"sample_count\": " << result.discarded_samples
           << ", \"included_in_overflow_and_clipping_statistics\": false},\n"
           << "  \"requested_sample_count\": " << request.sample_count
           << ",\n  \"retained_sample_count\": " << result.retained_samples
           << ",\n  \"read_call_count\": " << result.read_calls
           << ",\n  \"partial_read_count\": " << result.partial_reads
           << ",\n  \"timeout_count\": " << result.timeout_count
           << ",\n  \"overflow_count\": " << result.overflow_count
           << ",\n  \"clipping\": {\"threshold\": " << request.clipping_threshold
           << ", \"sample_count\": " << result.clipped_samples << "},\n"
           << "  \"output\": {\"path\": \"" << escape_json(request.output_path.generic_u8string())
           << "\", \"present\": " << result.output_present
           << ", \"complete\": " << result.output_complete
           << ", \"size_bytes\": " << result.output_bytes << ", \"sha256\": ";
    string_or_null(output, result.output_sha256);
    output << ", \"removed_incomplete_size_bytes\": "
           << result.removed_incomplete_size_bytes << ", \"removed_incomplete_sha256\": ";
    string_or_null(output, result.removed_incomplete_sha256);
    output << "},\n  \"primary_outcome\": \"" << result.primary_outcome
           << "\",\n  \"primary_failure_cause\": ";
    string_or_null(output, result.primary_failure_cause);
    output << ",\n  \"failure_causes\": "; write_string_array(output, result.failure_causes);
    output << ",\n  \"cleanup\": {\"outcome\": \"" << result.cleanup_outcome
           << "\", \"attempted_steps\": "; write_string_array(output, result.cleanup.attempted_steps);
    output << ", \"failed_steps\": "; write_string_array(output, result.cleanup.failed_steps);
    output << "},\n  \"process_exit_code\": " << result.exit_code << "\n}\n";
    output.close();
    if (!output) throw std::runtime_error("cannot complete evidence");
    if (std::filesystem::exists(destination))
        throw std::runtime_error("evidence destination appeared during capture");
    std::filesystem::rename(temporary, destination);
}

std::string setting_mismatch(const Settings& requested, const Settings& actual) {
    if (requested.driver != actual.driver || requested.serial != actual.serial) return "identity";
    if (requested.format != actual.format || requested.channel != actual.channel ||
        requested.agc != actual.agc || requested.bias_tee != actual.bias_tee ||
        !close(requested.sample_rate_hz, actual.sample_rate_hz) ||
        !close(requested.bandwidth_hz, actual.bandwidth_hz) ||
        !close(requested.center_frequency_hz, actual.center_frequency_hz) ||
        !close(requested.gain_db, actual.gain_db)) return "settings";
    return {};
}

void append_float_le(std::vector<unsigned char>& bytes, float value) {
    std::uint32_t bits = 0;
    std::memcpy(&bits, &value, sizeof(value));
    bytes.push_back(static_cast<unsigned char>(bits & 0xffU));
    bytes.push_back(static_cast<unsigned char>((bits >> 8U) & 0xffU));
    bytes.push_back(static_cast<unsigned char>((bits >> 16U) & 0xffU));
    bytes.push_back(static_cast<unsigned char>((bits >> 24U) & 0xffU));
}

void write_cf32(std::ofstream& output, const std::complex<float>* samples, std::size_t count) {
    if (count > std::numeric_limits<std::size_t>::max() / cf32_bytes_per_sample)
        throw std::overflow_error("CF32 buffer size overflow");
    std::vector<unsigned char> bytes;
    bytes.reserve(count * cf32_bytes_per_sample);
    for (std::size_t index = 0; index < count; ++index) {
        append_float_le(bytes, samples[index].real());
        append_float_le(bytes, samples[index].imag());
    }
    output.write(reinterpret_cast<const char*>(bytes.data()),
                 static_cast<std::streamsize>(bytes.size()));
}

}  // namespace

double SystemClock::monotonic_seconds() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

std::string SystemClock::utc_now() {
    const auto now = std::chrono::system_clock::now();
    const auto milliseconds = std::chrono::duration_cast<std::chrono::milliseconds>(
        now.time_since_epoch()).count() % 1000;
    const std::time_t value = std::chrono::system_clock::to_time_t(now);
    const std::tm* utc = std::gmtime(&value);
    if (utc == nullptr) throw std::runtime_error("cannot format UTC timestamp");
    std::ostringstream output;
    output << std::put_time(utc, "%Y-%m-%dT%H:%M:%S") << '.' << std::setw(3)
           << std::setfill('0') << milliseconds << 'Z';
    return output.str();
}

CleanupReport run_cleanup_steps(CleanupActions& actions) noexcept {
    CleanupReport report;
    const auto attempt = [&](const std::string& name, auto operation) {
        report.attempted_steps.push_back(name);
        try { operation(); }
        catch (...) { report.failed_steps.push_back(name); }
    };
    if (actions.needs_deactivate()) attempt("deactivate_stream", [&] { actions.deactivate(); });
    if (actions.needs_close()) attempt("close_stream", [&] { actions.close(); });
    if (actions.needs_release()) attempt("release_device", [&] { actions.release(); });
    return report;
}

CaptureResult capture_exact(SampleSource& source, const CaptureRequest& request) {
    CaptureResult result;
    SystemClock system_clock;
    Clock& clock = request.clock == nullptr ? static_cast<Clock&>(system_clock) : *request.clock;
    const auto incomplete = std::filesystem::path(request.output_path.string() + ".incomplete");
    const auto success_incomplete = std::filesystem::path(request.metadata_path.string() + ".incomplete");
    const auto failure_incomplete = std::filesystem::path(request.failure_metadata_path.string() + ".incomplete");
    const bool size_overflow = request.sample_count >
        std::numeric_limits<std::size_t>::max() / cf32_bytes_per_sample;
    const bool chunk_overflow = request.chunk_samples >
            std::numeric_limits<std::size_t>::max() / sizeof(std::complex<float>) ||
        request.chunk_samples > static_cast<std::size_t>(
            std::numeric_limits<std::streamsize>::max()) / cf32_bytes_per_sample;
    const bool invalid_text = !valid_utf8(request.capture_id) ||
        !valid_utf8(request.requested.driver) || !valid_utf8(request.requested.serial) ||
        !valid_utf8(request.requested.format) || !valid_utf8(request.output_path.generic_u8string()) ||
        !valid_utf8(request.metadata_path.generic_u8string()) ||
        !valid_utf8(request.failure_metadata_path.generic_u8string());
    if (request.capture_id.empty() || request.sample_count == 0 || request.chunk_samples == 0 ||
        size_overflow || chunk_overflow || invalid_text || request.output_path.empty() ||
        request.metadata_path.empty() ||
        request.failure_metadata_path.empty() || request.output_path == request.metadata_path ||
        request.output_path == request.failure_metadata_path ||
        request.metadata_path == request.failure_metadata_path || request.max_read_calls < 2 ||
        request.max_elapsed_duration_s <= 0.0 || request.read_timeout_us <= 0 ||
        request.clipping_threshold <= 0.0F || request.clipping_threshold > 1.0F ||
        !std::isfinite(request.max_elapsed_duration_s) ||
        !std::isfinite(request.clipping_threshold) || !finite_settings(request.requested) ||
        std::filesystem::exists(request.output_path) || std::filesystem::exists(request.metadata_path) ||
        std::filesystem::exists(request.failure_metadata_path) || std::filesystem::exists(incomplete) ||
        std::filesystem::exists(success_incomplete) || std::filesystem::exists(failure_incomplete)) {
        result.exit_code = invalid_arguments;
        result.primary_failure_cause = "invalid_arguments";
        result.failure_causes.push_back("invalid_arguments");
        return result;
    }

    const double started = clock.monotonic_seconds();
    result.timestamps.helper_start = clock.utc_now();
    auto fail = [&](int code, const std::string& cause) {
        if (result.primary_failure_cause.empty()) {
            result.primary_failure_cause = cause;
            result.exit_code = code;
            result.primary_outcome = code == cancelled ? "cancelled" : "failed";
        }
        append_unique(result.failure_causes, cause);
    };
    auto deadline = [&]() {
        if (clock.monotonic_seconds() - started >= request.max_elapsed_duration_s) {
            fail(timed_out, "elapsed_time_limit");
            return true;
        }
        return false;
    };

    bool finalizing_output = false;
    try {
        result.timestamps.configuration_start = clock.utc_now();
        if (!deadline()) {
            result.actual = source.configure(request.requested);
            result.actual_available = true;
        }
        result.timestamps.configuration_complete = clock.utc_now();
        deadline();
        if (result.primary_failure_cause.empty() && !finite_settings(result.actual)) {
            result.actual_available = false;
            fail(settings_mismatch, "non_finite_actual_settings");
        }
        if (result.primary_failure_cause.empty()) {
            const auto mismatch = setting_mismatch(request.requested, result.actual);
            if (!mismatch.empty())
                fail(mismatch == "identity" ? identity_mismatch : settings_mismatch,
                     mismatch == "identity" ? "wrong_device" : "settings_mismatch");
        }

        std::vector<std::complex<float>> buffer;
        if (result.primary_failure_cause.empty()) buffer.resize(request.chunk_samples);
        if (result.primary_failure_cause.empty()) {
            result.timestamps.first_read_start = clock.utc_now();
            if (!deadline()) {
                result.first_read_attempted = true;
                const auto first = source.read(buffer.data(), buffer.size());
                ++result.read_calls;
                result.timestamps.first_read_complete = clock.utc_now();
                deadline();
                if (result.primary_failure_cause.empty() &&
                    (first.kind != ReadKind::samples || first.count == 0 || first.count > buffer.size()))
                    fail(capture_failed, "first_read_failed");
                else if (result.primary_failure_cause.empty()) result.discarded_samples = first.count;
            }
        }

        std::ofstream output;
        if (result.primary_failure_cause.empty()) {
            output.open(incomplete, std::ios::binary | std::ios::trunc);
            if (!output) throw std::runtime_error("cannot create incomplete output");
            result.timestamps.retained_capture_start = clock.utc_now();
        }
        while (result.primary_failure_cause.empty() && result.retained_samples < request.sample_count) {
            if (result.read_calls >= request.max_read_calls) { fail(timed_out, "read_call_limit"); break; }
            if (deadline()) break;
            const auto capacity = std::min(request.chunk_samples,
                                           request.sample_count - result.retained_samples);
            const auto read = source.read(buffer.data(), capacity);
            ++result.read_calls;
            if (deadline()) break;
            if (read.kind == ReadKind::timeout) {
                ++result.timeout_count;
                if (result.timeout_count > request.max_timeouts) fail(timed_out, "timeout");
                continue;
            }
            if (read.kind == ReadKind::overflow) {
                ++result.overflow_count;
                if (result.overflow_count > request.max_overflows) fail(overflow_rejected, "overflow");
                continue;
            }
            if (read.kind == ReadKind::cancelled) { fail(cancelled, "cancelled"); continue; }
            if (read.kind != ReadKind::samples || read.count == 0) { fail(capture_failed, "short_read"); continue; }
            if (read.count > capacity) { fail(capture_failed, "impossible_read_count"); continue; }
            if (read.count < capacity) ++result.partial_reads;
            for (std::size_t index = 0; index < read.count; ++index) {
                if (!std::isfinite(buffer[index].real()) || !std::isfinite(buffer[index].imag()) ||
                    std::abs(buffer[index].real()) >= request.clipping_threshold ||
                    std::abs(buffer[index].imag()) >= request.clipping_threshold)
                    ++result.clipped_samples;
            }
            write_cf32(output, buffer.data(), read.count);
            if (!output) throw std::runtime_error("cannot write incomplete output");
            result.retained_samples += read.count;
        }
        if (output.is_open()) {
            output.close();
            result.timestamps.retained_capture_complete = clock.utc_now();
        }
        if (result.primary_failure_cause.empty() && result.clipped_samples != 0)
            fail(clipping_rejected, "clipping");
        if (result.primary_failure_cause.empty()) {
            finalizing_output = true;
            result.output_bytes = static_cast<std::size_t>(std::filesystem::file_size(incomplete));
            const auto expected = request.sample_count * cf32_bytes_per_sample;
            if (result.output_bytes != expected || result.retained_samples != request.sample_count)
                fail(output_invalid, "incomplete_output");
            else {
                if (std::filesystem::exists(request.output_path))
                    throw std::runtime_error("output destination appeared during capture");
                std::filesystem::rename(incomplete, request.output_path);
                result.output_present = true;
                result.output_complete = true;
                result.output_sha256 = sha256_file(request.output_path);
            }
        }
    } catch (const std::exception&) {
        fail(finalizing_output ? output_invalid : capture_failed,
             finalizing_output ? "output_finalization_failed" : "io_or_source_error");
    }

    result.timestamps.cleanup_start = clock.utc_now();
    deadline();
    result.cleanup = source.cleanup();
    result.timestamps.cleanup_complete = clock.utc_now();
    deadline();
    if (!result.cleanup.failed_steps.empty()) {
        result.cleanup_outcome = "failed";
        append_unique(result.failure_causes, "cleanup");
        if (result.primary_failure_cause.empty()) result.primary_failure_cause = "cleanup";
        result.primary_outcome = "failed";
        result.exit_code = cleanup_failed;
    }

    if (result.primary_failure_cause.empty()) {
        result.exit_code = success;
        result.primary_outcome = "success";
    } else if (result.exit_code == success) {
        result.exit_code = capture_failed;
    }
    if (result.exit_code != success) {
        if (std::filesystem::exists(incomplete)) {
            try {
                result.removed_incomplete_size_bytes =
                    static_cast<std::size_t>(std::filesystem::file_size(incomplete));
                result.removed_incomplete_sha256 = sha256_file(incomplete);
            } catch (const std::exception&) {
                append_unique(result.failure_causes, "incomplete_inspection_failed");
            }
        }
        remove_if_present(incomplete);
        if (result.output_present) remove_if_present(request.output_path);
        result.output_present = false;
        result.output_complete = false;
        result.output_bytes = 0;
        result.output_sha256.clear();
    }
    result.timestamps.helper_complete = clock.utc_now();
    result.elapsed_duration_s = clock.monotonic_seconds() - started;

    const auto destination = result.exit_code == success ? request.metadata_path
                                                         : request.failure_metadata_path;
    try {
        write_evidence(request, result, destination);
        result.evidence_path = destination;
    } catch (const std::exception& error) {
        remove_if_present(std::filesystem::path(destination.string() + ".incomplete"));
        result.evidence_error = error.what();
        if (result.primary_failure_cause.empty()) result.primary_failure_cause = "evidence_write_failed";
        append_unique(result.failure_causes, "evidence_write_failed");
        result.primary_outcome = "failed";
        if (result.cleanup_outcome != "failed") result.exit_code = evidence_failed;
        if (result.output_present) remove_if_present(request.output_path);
        result.output_present = false;
        result.output_complete = false;
    }
    return result;
}

}  // namespace wspq

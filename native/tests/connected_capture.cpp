// Test-only sample source. No SoapySDR or hardware libraries are linked.
// Uses the production helper argv and capture_exact writer, including cleanup.
#include "wsprrypi_capture/capture.hpp"
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <thread>

namespace {
class Source final : public wspq::SampleSource, public wspq::Clock {
  std::filesystem::path input;
  std::filesystem::path cancellation;
  std::int64_t epoch_ms{};
  double rate{1};
  std::size_t retained{}, sequence{};
  bool first{true};
  const std::chrono::steady_clock::time_point wall_deadline =
      std::chrono::steady_clock::now() + std::chrono::seconds(8);
  void acknowledge() {
    std::ofstream(input / ("ack-" + std::to_string(sequence))).put('\n');
  }

public:
  Source(std::filesystem::path path, std::filesystem::path stop)
      : input(std::move(path)), cancellation(std::move(stop)) {
    std::ifstream start(input / "start-ms");
    if (!(start >> epoch_ms))
      throw std::runtime_error("missing fixture epoch");
  }
  wspq::Settings configure(const wspq::Settings &settings) override {
    if (settings.driver != "fixture")
      throw std::runtime_error("fixture driver required");
    rate = settings.sample_rate_hz;
    return settings;
  }
  wspq::ReadResult read(std::complex<float> *destination,
                        std::size_t capacity) override {
    if (std::chrono::steady_clock::now() >= wall_deadline)
      return {wspq::ReadKind::error, 0};
    if (first) {
      first = false;
      destination[0] = {};
      return {wspq::ReadKind::samples, 1};
    }
    // The previous call has returned through the production writer before this
    // ACK.
    acknowledge();
    const auto deadline =
        std::min(wall_deadline,
                 std::chrono::steady_clock::now() + std::chrono::seconds(5));
    const auto chunk = input / ("chunk-" + std::to_string(sequence));
    while (!std::filesystem::exists(chunk)) {
      if (std::filesystem::exists(cancellation))
        return {wspq::ReadKind::cancelled, 0};
      if (std::filesystem::exists(input / "crash")) {
        std::cerr << "fixture: abrupt exit after confirmed running; no native "
                     "cleanup\n"
                  << std::flush;
        std::_Exit(71);
      }
      if (std::filesystem::exists(input / "fail"))
        return {wspq::ReadKind::error, 0};
      if (std::chrono::steady_clock::now() >= deadline)
        return {wspq::ReadKind::error, 0};
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    const auto bytes = std::filesystem::file_size(chunk);
    if (bytes == 0 || bytes % 8 != 0 || bytes / 8 > capacity)
      throw std::runtime_error("invalid fixture chunk");
    std::ifstream stream(chunk, std::ios::binary);
    for (std::size_t i = 0; i < bytes / 8; ++i) {
      float components[2]{};
      for (float &component : components) {
        unsigned char raw[4]{};
        stream.read(reinterpret_cast<char *>(raw), 4);
        std::uint32_t bits =
            std::uint32_t(raw[0]) | (std::uint32_t(raw[1]) << 8U) |
            (std::uint32_t(raw[2]) << 16U) | (std::uint32_t(raw[3]) << 24U);
        std::memcpy(&component, &bits, sizeof(component));
      }
      destination[i] = {components[0], components[1]};
    }
    if (!stream)
      throw std::runtime_error("short fixture chunk");
    retained += static_cast<std::size_t>(bytes / 8);
    ++sequence;
    return {wspq::ReadKind::samples, static_cast<std::size_t>(bytes / 8)};
  }
  wspq::CleanupReport cleanup() noexcept override {
    std::error_code error;
    if (std::filesystem::exists(input / "cleanup-fail", error))
      return {{"release_fixture_source"}, {"release_fixture_source"}};
    return {{"release_fixture_source"}, {}};
  }
  double monotonic_seconds() override {
    return static_cast<double>(retained) / rate;
  }
  std::string utc_now() override {
    const auto ms =
        epoch_ms + (rate > 0
                        ? static_cast<std::int64_t>(
                              1000.0 * static_cast<double>(retained) / rate)
                        : 0);
    const auto seconds = static_cast<std::time_t>(ms / 1000);
    const auto *utc = std::gmtime(&seconds);
    if (!utc)
      throw std::runtime_error("fixture UTC formatting failed");
    std::ostringstream out;
    out << std::put_time(utc, "%Y-%m-%dT%H:%M:%S") << '.' << std::setw(3)
        << std::setfill('0') << ms % 1000 << 'Z';
    return out.str();
  }
};
} // namespace
int main(int argc, char **argv) {
  try {
    if (argc != 17 || std::string(argv[1]) != "--enable-physical-sdr" ||
        std::string(argv[2]) != "fixture")
      throw std::runtime_error(
          "test-only helper requires production argv and fixture driver");
    wspq::CaptureRequest request;
    request.requested.driver = argv[2];
    request.requested.serial = argv[3];
    request.requested.center_frequency_hz = std::stod(argv[4]);
    request.sample_count = std::stoull(argv[5]);
    request.requested.gain_db = std::stod(argv[6]);
    request.requested.sample_rate_hz = std::stod(argv[7]);
    request.requested.bandwidth_hz = std::stod(argv[8]);
    request.requested.channel = std::stoi(argv[9]);
    request.requested.agc = std::string(argv[10]) == "true";
    request.requested.bias_tee = std::string(argv[11]) == "true";
    request.read_timeout_us = std::stol(argv[12]);
    request.max_elapsed_duration_s = std::stod(argv[13]);
    request.output_path = std::filesystem::u8path(argv[14]);
    request.metadata_path = std::filesystem::u8path(argv[15]);
    request.failure_metadata_path =
        std::filesystem::u8path(std::string(argv[15]) + ".failure.json");
    request.capture_id = argv[16];
    request.cancellation_path = std::filesystem::u8path(std::string(argv[15]) + ".cancel");
    Source source(std::filesystem::u8path(std::string(argv[14]) + ".source"),
                  request.cancellation_path);
    request.clock = &source;
    const auto result = wspq::capture_exact(source, request);
    return result.exit_code;
  } catch (const std::exception &error) {
    std::cerr << error.what() << '\n';
    return 2;
  }
}

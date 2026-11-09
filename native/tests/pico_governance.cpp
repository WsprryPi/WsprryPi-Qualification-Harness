// Hardware-free boundary: production WTP endpoint and JobService with a
// recording engine. No physical adapter, SDK, socket, USB, or GPIO
// implementation is linked.
#include "pico_encoder_identity.hpp"
#include "wtp/codec.hpp"
#include "wtp/endpoint.hpp"
#include "wtp/inhibited_rf_engine.hpp"
#include <algorithm>
#include <iostream>
using namespace wsprrypico::wtp;
constexpr std::uint64_t epoch = 1786449600000000000ULL;
struct FixtureClock : Clock {
  ClockSnapshot value{ClockState::Synchronized, epoch, 0, 100, 0,
                      LeapState::Normal,        {}};
  ClockSnapshot snapshot() const override { return value; }
};
struct Identity : IdentitySource {
  std::string new_boot_id() override { return std::string(32, 'b'); }
};
struct Execution {
  Job job;
  std::uint64_t start, until;
};
struct RecordingEngine : RfEngine {
  FixtureClock &clock;
  InhibitedRfEngine inhibited;
  std::vector<Execution> executions;
  bool active = false;
  explicit RecordingEngine(FixtureClock &c) : clock(c) {}
  PrepareResult prepare(const Job &job) override {
    return inhibited.prepare(job);
  }
  bool begin(const Job &job, std::uint64_t start) override {
    if (!inhibited.begin(job, start))
      return false;
    executions.push_back({job, start, start});
    active = true;
    return true;
  }
  void record(std::uint64_t now) {
    if (active) {
      auto &run = executions.back();
      run.until = std::max(
          run.start, std::min(now, run.start + run.job.total_duration_ns));
    }
  }
  EngineReport poll(std::uint64_t now) override {
    record(now);
    return inhibited.poll(now);
  }
  bool disable(std::uint64_t deadline) override {
    record(clock.value.monotonic_now_ns);
    active = false;
    return inhibited.disable(deadline);
  }
  bool output_active() const override { return false; }
};
std::vector<std::uint8_t> unhex(std::string_view s) {
  std::vector<std::uint8_t> result;
  auto digit = [](char c) { return c <= '9' ? c - '0' : c - 'a' + 10; };
  for (std::size_t i = 0; i + 1 < s.size(); i += 2)
    result.push_back(
        static_cast<std::uint8_t>((digit(s[i]) << 4) | digit(s[i + 1])));
  return result;
}
std::string hex(std::span<const std::uint8_t> bytes) {
  std::string result;
  constexpr char digits[] = "0123456789abcdef";
  for (auto b : bytes) {
    result += digits[b >> 4];
    result += digits[b & 15];
  }
  return result;
}
int main() {
  FixtureClock clock;
  Identity identity;
  RecordingEngine engine(clock);
  ServiceConfig config;
  config.capability_engine = "pio-dma-gp2";
  JobService service(clock, engine, identity, config);
  Endpoint endpoint(service, "1a528102027ce5a44fcb0b2d85b5e9e9",
                    "governance-fixture");
  std::string line;
  while (std::getline(std::cin, line)) {
    auto value = json::parse(line);
    if (!value)
      return 2;
    auto get = [&](std::string_view k) {
      return value->get(k).value_or(json::Value{});
    };
    const auto action = get("action").string();
    if (action == "connect")
      endpoint.connect("usb-physical");
    else if (action == "clock") {
      if (!json::decimal(get("now_ns"), clock.value.monotonic_now_ns))
        return 3;
      clock.value.utc_now_ns = epoch + clock.value.monotonic_now_ns;
    } else if (action == "disconnect")
      endpoint.disconnect();
    else if (action != "send" && action != "inspect")
      return 4;
    auto now = clock.value.monotonic_now_ns / 1000000ULL;
    endpoint.poll(now);
    auto input = unhex(get("hex").string());
    std::vector<std::uint8_t> output;
    for (unsigned i = 0; i < 100000; ++i) {
      bool progress = false;
      if (!input.empty() && endpoint.can_receive()) {
        auto n = endpoint.receive(input, now);
        input.erase(input.begin(),
                    input.begin() + static_cast<std::ptrdiff_t>(n));
        progress = n != 0;
      }
      if (!endpoint.output().empty()) {
        auto bytes = endpoint.output();
        output.insert(output.end(), bytes.begin(), bytes.end());
        endpoint.consume_output(bytes.size(), now);
        progress = true;
      }
      if (!progress)
        break;
    }
    std::cout << "{\"hex\":\"" << hex(output)
              << "\",\"closed\":" << (endpoint.closed() ? "true" : "false")
              << ",\"executions\":[";
    if (action == "inspect") {
      bool first = true;
      for (const auto &run : engine.executions) {
        if (!first)
          std::cout << ',';
        first = false;
        std::cout << "{\"job_id\":" << json::quote(run.job.job_id)
                  << ",\"start_ns\":\"" << run.start << "\",\"until_ns\":\""
                  << run.until << "\",\"events\":[";
        bool first_event = true;
        for (const auto &e : run.job.events) {
          if (!first_event)
            std::cout << ',';
          first_event = false;
          std::cout << "{\"offset_ns\":\"" << e.offset_ns
                    << "\",\"duration_ns\":\"" << e.duration_ns
                    << "\",\"rf_on\":" << (e.rf_on ? "true" : "false")
                    << ",\"frequency_nhz\":\"" << e.frequency_nhz.value_or(0)
                    << "\"}";
        }
        std::cout << "]}";
      }
    }
    std::cout << "],\"fixture_contract\":\"wspq-pico-governance/"
                 "1\",\"source_revision\":\""
              << WSPQ_PICO_REVISION
              << "\",\"source_files\":" << wspq_pico_encoder_inputs << "}\n"
              << std::flush;
  }
  endpoint.disconnect();
}

// MIT. Compile complete WTP jobs with the selected Pico's production encoders.
// stdin/stdout are a bounded offline compiler interface, never a device protocol.
#include "encoding/message_wire.hpp"
#include "encoding/wspr.hpp"
#include "pico_encoder_identity.hpp"
#include <iostream>

using namespace wsprrypico;

namespace {
std::string serialize(const wtp::Job& job) {
    using wtp::json::quote;
    auto ns = [](std::uint64_t n) { return wtp::json::quote(std::to_string(n)); };
    std::string out = "{\"job_id\":" + quote(job.job_id) +
        ",\"profile\":\"rf-events/1\",\"mode\":" + quote(job.mode) +
        ",\"allow_frequency_adjustment\":" + (job.allow_frequency_adjustment ? "true" : "false") +
        ",\"total_duration_ns\":" + ns(job.total_duration_ns) + ",\"events\":[";
    bool first = true;
    for (const auto& e : job.events) {
        if (!first) out += ',';
        first = false;
        out += "{\"offset_ns\":" + ns(e.offset_ns) + ",\"duration_ns\":" + ns(e.duration_ns) +
               ",\"rf_on\":" + (e.rf_on ? "true" : "false");
        if (e.rf_on) out += ",\"frequency_nhz\":" + ns(*e.frequency_nhz);
        out += '}';
    }
    return out + "]}";
}
}

int main() {
    std::string text;
    char c;
    while (std::cin.get(c)) {
        if (text.size() >= 32768) return 2;
        text += c;
    }
    const auto root = wtp::json::parse(text);
    if (!root) return 2;
    auto get = [&](std::string_view key) { return root->get(key).value_or(wtp::json::Value{}); };
    const auto mode = get("mode").string();
    std::string jobs;
    if (mode == "qrss" || mode == "fskcw" || mode == "dfcw") {
        const auto message = encoding::decode_message(*root);
        if (!message) return 2;
        const auto compiled = encoding::compile_message(*message);
        if (!compiled.job) return 2;
        jobs = serialize(*compiled.job);
    } else if (mode == "tone" || mode == "wspr") {
        const bool tone = mode == "tone";
        if (tone ? !wtp::json::fields(*root, {"mode", "job_id", "frequency_nhz", "duration_ns", "off_ns", "cycles",
                                            "allow_frequency_adjustment"})
                 : !wtp::json::fields(*root, {"mode", "job_id", "frequency_nhz", "callsign",
                                            "locator", "power_dbm", "allow_frequency_adjustment"})) return 2;
        std::uint64_t frequency = 0, duration = 0, off = 0;
        if (!wtp::json::identifier(get("job_id")) ||
            !wtp::json::decimal(get("frequency_nhz"), frequency, true) ||
            (get("allow_frequency_adjustment").raw != "true" &&
             get("allow_frequency_adjustment").raw != "false")) return 2;
        std::optional<encoding::WsprPlan> plan;
        if (tone) {
            if (!wtp::json::decimal(get("duration_ns"), duration, true) ||
                !wtp::json::decimal(get("off_ns"), off, true) || get("cycles").raw != "3" ||
                duration > 60'000'000'000ULL || off > 60'000'000'000ULL) return 2;
        } else {
            const auto power = get("power_dbm");
            if (get("callsign").type() != '"' || get("locator").type() != '"' ||
                power.raw.empty() || power.raw.size() > 2 ||
                !std::all_of(power.raw.begin(), power.raw.end(), [](char n) {
                    return n >= '0' && n <= '9';
                })) return 2;
            plan = encoding::wspr_plan(get("callsign").string(), get("locator").string(),
                                       static_cast<unsigned>(get("power_dbm").integer()));
            if (!plan) return 2;
        }
        const auto count = tone ? 1U : plan->count;
        for (unsigned frame = 0; frame < count; ++frame) {
            wtp::Job job;
            job.job_id = get("job_id").string();
            job.mode = mode;
            job.allow_frequency_adjustment = get("allow_frequency_adjustment").boolean();
            const auto event_count = tone ? 6U : 162U;
            if (!job.events.reserve(event_count)) return 2;
            for (unsigned i = 0; i < event_count; ++i) {
                auto boundary = [](unsigned n) {
                    return (static_cast<std::uint64_t>(n) * 8192 * 1'000'000'000ULL + 6000) / 12000;
                };
                const bool on = !tone || i % 2 == 0;
                const auto begin = tone ? job.total_duration_ns : boundary(i);
                const auto end = tone ? begin + (on ? duration : i == 5 ? 1000 : off) : boundary(i + 1);
                const auto delta = tone ? 0ULL : static_cast<std::uint64_t>(plan->frames[frame].symbols[i]) * 1'464'843'750;
                if (frequency > UINT64_MAX - delta) return 2;
                if (!job.events.push_back({begin, end - begin, on,
                    on ? std::optional(frequency + delta) : std::nullopt})) return 2;
                job.total_duration_ns = end;
            }
            if (frame) jobs += ',';
            jobs += serialize(job);
        }
    } else return 2;
    std::cout << "{\"compiler_contract\":\"wspq-pico-jobs/1\",\"source_revision\":\"" << WSPQ_PICO_REVISION
              << "\",\"source_files\":" << wspq_pico_encoder_inputs << ",\"jobs\":[" << jobs << "]}\n";
}

#include "wsprrypi_capture/capture.hpp"

#include <chrono>
#include <complex>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <limits>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

class FakeClock final : public wspq::Clock {
public:
    double monotonic_seconds() override { return monotonic_; }
    std::string utc_now() override {
        const auto value = utc_index_++;
        return "2026-08-11T12:00:" + std::string(value < 10 ? "0" : "") +
               std::to_string(value) + ".000Z";
    }
    void advance(double seconds) { monotonic_ += seconds; }
private:
    double monotonic_{0.0};
    unsigned utc_index_{0};
};

struct Event {
    wspq::ReadKind kind;
    std::size_t count;
    float real{0.25F};
    float imaginary{-0.25F};
    double advance_s{0.0};
};

class ScriptSource final : public wspq::SampleSource {
public:
    ScriptSource(FakeClock& clock, std::vector<Event> events)
        : clock_(clock), events_(std::move(events)) {}
    wspq::Settings configure(const wspq::Settings& requested) override {
        clock_.advance(configure_advance_s);
        auto actual = requested;
        if (configure_mutation != nullptr) configure_mutation(actual);
        return actual;
    }
    wspq::ReadResult read(std::complex<float>* output, std::size_t capacity) override {
        if (next_ == events_.size()) return {wspq::ReadKind::end, 0};
        const auto event = events_[next_++];
        clock_.advance(event.advance_s);
        const auto fill = std::min(event.count, capacity);
        for (std::size_t index = 0; index < fill; ++index)
            output[index] = {event.real, event.imaginary};
        return {event.kind, event.count};
    }
    wspq::CleanupReport cleanup() noexcept override {
        clock_.advance(cleanup_advance_s);
        ++cleanup_calls;
        return cleanup_report;
    }
    void (*configure_mutation)(wspq::Settings&) = nullptr;
    double configure_advance_s{0.0};
    double cleanup_advance_s{0.0};
    std::size_t cleanup_calls{0};
    wspq::CleanupReport cleanup_report{{"mock_release"}, {}};
private:
    FakeClock& clock_;
    std::vector<Event> events_;
    std::size_t next_{0};
};

struct TestRoot {
    TestRoot() {
        const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
        path = std::filesystem::temp_directory_path() / ("wspq-capture-tests-" + std::to_string(stamp));
        std::filesystem::create_directories(path);
    }
    ~TestRoot() { std::error_code error; std::filesystem::remove_all(path, error); }
    std::filesystem::path path;
};

void require(bool condition, const std::string& message) {
    if (!condition) throw std::runtime_error(message);
}

wspq::CaptureRequest request(const std::filesystem::path& root, FakeClock& clock,
                             std::string stem = "capture") {
    wspq::CaptureRequest value;
    value.capture_id = "20260811T120000Z-mock-capture";
    value.output_path = root / (stem + ".cf32");
    value.metadata_path = root / (stem + ".json");
    value.failure_metadata_path = root / (stem + ".failure.json");
    value.sample_count = 10;
    value.chunk_samples = 4;
    value.clock = &clock;
    return value;
}

std::string read_text(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}

void success_exact_partial_and_discard() {
    TestRoot root; FakeClock clock;
    ScriptSource source(clock, {{wspq::ReadKind::samples,3}, {wspq::ReadKind::samples,2},
                                {wspq::ReadKind::samples,4}, {wspq::ReadKind::samples,4}});
    const auto value = request(root.path, clock);
    const auto result = wspq::capture_exact(source, value);
    require(result.exit_code == 0, "success capture failed");
    require(result.discarded_samples == 3, "first read was not discarded");
    require(result.retained_samples == 10 && result.partial_reads == 1, "exact partial contract failed");
    require(result.output_bytes == 80 && std::filesystem::file_size(value.output_path) == 80,
            "CF32 byte count is wrong");
    require(result.output_sha256.size() == 64, "SHA-256 missing");
    require(std::filesystem::exists(value.metadata_path), "success evidence missing");
    require(!std::filesystem::exists(value.failure_metadata_path), "failure evidence on success");
}

void golden_little_endian_cf32() {
    TestRoot root; FakeClock clock;
    ScriptSource source(clock, {{wspq::ReadKind::samples,1,0.0F,0.0F},
                                {wspq::ReadKind::samples,1,0.5F,-0.5F},
                                {wspq::ReadKind::samples,1,0.25F,-0.25F}});
    auto value = request(root.path, clock, "golden"); value.sample_count=2; value.chunk_samples=1;
    const auto result = wspq::capture_exact(source, value);
    require(result.exit_code == 0, "golden capture failed");
    const std::vector<unsigned char> expected = {
        0x00,0x00,0x00,0x3f, 0x00,0x00,0x00,0xbf,
        0x00,0x00,0x80,0x3e, 0x00,0x00,0x80,0xbe};
    std::ifstream input(value.output_path, std::ios::binary);
    const std::vector<unsigned char> actual{std::istreambuf_iterator<char>(input),
                                             std::istreambuf_iterator<char>()};
    require(actual == expected, "CF32 wire bytes are not little-endian real/imaginary binary32");
    require(result.output_sha256 == "21f0baa17258fa40b058c5ac8819e65774c91fd5d549aa90974c55cf8c492b82",
            "golden native SHA-256 changed");
}

void paths_with_spaces_and_deterministic_hash() {
    TestRoot root; const auto spaced = root.path / "directory with spaces";
    std::filesystem::create_directory(spaced);
    FakeClock first_clock; FakeClock second_clock;
    const std::vector<Event> events{{wspq::ReadKind::samples,1},{wspq::ReadKind::samples,4},
                                    {wspq::ReadKind::samples,4},{wspq::ReadKind::samples,2}};
    ScriptSource one(first_clock, events); ScriptSource two(second_clock, events);
    const auto first = wspq::capture_exact(one, request(spaced, first_clock, "first file"));
    const auto second = wspq::capture_exact(two, request(spaced, second_clock, "second file"));
    require(first.exit_code == 0 && second.exit_code == 0, "space-path capture failed");
    require(first.output_sha256 == second.output_sha256, "mock bytes are not deterministic");
}

void expect_failure(const std::string& name, ScriptSource& source, const wspq::CaptureRequest& value,
                    int code, const std::string& cause) {
    const auto result = wspq::capture_exact(source, value);
    require(result.exit_code == code, name + " returned wrong exit code");
    require(result.primary_failure_cause == cause, name + " lost primary cause");
    require(!std::filesystem::exists(value.output_path), name + " left final IQ output");
    require(!std::filesystem::exists(value.metadata_path), name + " left success metadata");
    require(std::filesystem::exists(value.failure_metadata_path), name + " lacks failure evidence");
    require(read_text(value.failure_metadata_path).find("\"evidence_type\": \"capture_failure\"") !=
                std::string::npos, name + " evidence is not failure-identified");
    require(read_text(value.failure_metadata_path).find("\"primary_failure_cause\": \""+cause+"\"") !=
                std::string::npos, name + " failure evidence lost its primary cause");
}

void failure_contracts() {
    TestRoot root;
    {
        FakeClock clock; ScriptSource source(clock, {{wspq::ReadKind::samples,1},{wspq::ReadKind::end,0}});
        expect_failure("short", source, request(root.path,clock,"short"), wspq::capture_failed,"short_read");
    }
    {
        FakeClock clock; ScriptSource source(clock, {{wspq::ReadKind::samples,1},{wspq::ReadKind::overflow,0}});
        expect_failure("overflow",source,request(root.path,clock,"overflow"),wspq::overflow_rejected,"overflow");
    }
    {
        FakeClock clock; ScriptSource source(clock, {{wspq::ReadKind::samples,1},{wspq::ReadKind::timeout,0},
                                                     {wspq::ReadKind::timeout,0}});
        auto value=request(root.path,clock,"timeout"); value.max_timeouts=1;
        expect_failure("timeout",source,value,wspq::timed_out,"timeout");
    }
    {
        FakeClock clock; ScriptSource source(clock, {{wspq::ReadKind::samples,1},{wspq::ReadKind::cancelled,0}});
        expect_failure("cancel",source,request(root.path,clock,"cancel"),wspq::cancelled,"cancelled");
    }
    {
        FakeClock clock; ScriptSource source(clock, {{wspq::ReadKind::samples,1}});
        source.configure_mutation=[](wspq::Settings& settings){settings.serial="WRONG";};
        expect_failure("identity",source,request(root.path,clock,"identity"),wspq::identity_mismatch,"wrong_device");
    }
    {
        FakeClock clock; ScriptSource source(clock, {{wspq::ReadKind::samples,1}});
        source.configure_mutation=[](wspq::Settings& settings){settings.gain_db+=1.0;};
        expect_failure("gain",source,request(root.path,clock,"gain"),wspq::settings_mismatch,"settings_mismatch");
    }
    {
        FakeClock clock; ScriptSource source(clock, {{wspq::ReadKind::samples,1},
            {wspq::ReadKind::samples,4,1.0F,0.0F},{wspq::ReadKind::samples,4},{wspq::ReadKind::samples,2}});
        expect_failure("clip",source,request(root.path,clock,"clip"),wspq::clipping_rejected,"clipping");
    }
    {
        FakeClock clock; ScriptSource source(clock, {{wspq::ReadKind::samples,1},{wspq::ReadKind::samples,99}});
        expect_failure("impossible",source,request(root.path,clock,"impossible"),wspq::capture_failed,
                       "impossible_read_count");
    }
}

void deadline_checkpoints() {
    TestRoot root;
    {
        FakeClock clock; ScriptSource source(clock,{}); source.configure_advance_s=2.0;
        auto value=request(root.path,clock,"configure-time"); value.max_elapsed_duration_s=1.0;
        expect_failure("configure deadline",source,value,wspq::timed_out,"elapsed_time_limit");
    }
    {
        FakeClock clock; ScriptSource source(clock,{{wspq::ReadKind::samples,1,0.0F,0.0F,2.0}});
        auto value=request(root.path,clock,"first-time"); value.max_elapsed_duration_s=1.0;
        expect_failure("first deadline",source,value,wspq::timed_out,"elapsed_time_limit");
    }
    {
        FakeClock clock; ScriptSource source(clock,{{wspq::ReadKind::samples,1},
                                                    {wspq::ReadKind::samples,4,0.1F,-0.1F,2.0}});
        auto value=request(root.path,clock,"read-time"); value.max_elapsed_duration_s=1.0;
        expect_failure("read deadline",source,value,wspq::timed_out,"elapsed_time_limit");
    }
    {
        FakeClock clock; ScriptSource source(clock,{{wspq::ReadKind::samples,1},
            {wspq::ReadKind::samples,4},{wspq::ReadKind::samples,4},{wspq::ReadKind::samples,2}});
        source.cleanup_advance_s=2.0;
        auto value=request(root.path,clock,"cleanup-time"); value.max_elapsed_duration_s=1.0;
        expect_failure("cleanup deadline",source,value,wspq::timed_out,"elapsed_time_limit");
    }
}

class ScriptCleanup final : public wspq::CleanupActions {
public:
    bool needs_deactivate() const noexcept override { return deactivate_needed; }
    bool needs_close() const noexcept override { return close_needed; }
    bool needs_release() const noexcept override { return release_needed; }
    void deactivate() override { order.push_back("deactivate"); deactivate_needed=false; if(fail_deactivate) throw 1; }
    void close() override { order.push_back("close"); close_needed=false; if(fail_close) throw 1; }
    void release() override { order.push_back("release"); release_needed=false; if(fail_release) throw 1; }
    bool deactivate_needed{true}, close_needed{true}, release_needed{true};
    bool fail_deactivate{false}, fail_close{false}, fail_release{false};
    std::vector<std::string> order;
};

void cleanup_sequencing() {
    for (unsigned mask=1; mask<8; ++mask) {
        ScriptCleanup actions;
        actions.fail_deactivate=(mask&1U)!=0; actions.fail_close=(mask&2U)!=0; actions.fail_release=(mask&4U)!=0;
        const auto report=wspq::run_cleanup_steps(actions);
        require(actions.order==std::vector<std::string>({"deactivate","close","release"}),
                "cleanup stopped after a failure");
        const auto expected_failures=static_cast<std::size_t>((mask&1U)!=0)+
            static_cast<std::size_t>((mask&2U)!=0)+static_cast<std::size_t>((mask&4U)!=0);
        require(report.failed_steps.size()==expected_failures,
                "cleanup failure count is wrong");
        const auto repeated=wspq::run_cleanup_steps(actions);
        require(repeated.attempted_steps.empty(),"cleanup is not idempotent");
    }
}

void cleanup_precedence() {
    TestRoot root;
    {
        FakeClock clock; ScriptSource source(clock,{{wspq::ReadKind::samples,1},
            {wspq::ReadKind::samples,4},{wspq::ReadKind::samples,4},{wspq::ReadKind::samples,2}});
        source.cleanup_report={{"deactivate","close","release"},{"close"}};
        const auto result=wspq::capture_exact(source,request(root.path,clock,"cleanup-success"));
        require(result.exit_code==wspq::cleanup_failed && result.primary_failure_cause=="cleanup",
                "cleanup did not override success");
    }
    {
        FakeClock clock; ScriptSource source(clock,{{wspq::ReadKind::samples,1},{wspq::ReadKind::end,0}});
        source.cleanup_report={{"deactivate","close","release"},{"release"}};
        const auto result=wspq::capture_exact(source,request(root.path,clock,"cleanup-failure"));
        require(result.exit_code==wspq::cleanup_failed && result.primary_failure_cause=="short_read",
                "cleanup erased the original failure");
        require(result.cleanup_outcome=="failed", "cleanup outcome missing");
    }
}

void preserve_existing_incomplete() {
    TestRoot root; FakeClock clock;
    const auto value=request(root.path,clock,"preserve");
    const auto incomplete=std::filesystem::path(value.output_path.string()+".incomplete");
    {std::ofstream output(incomplete); output<<"preserve me";}
    ScriptSource source(clock,{{wspq::ReadKind::samples,1}});
    const auto result=wspq::capture_exact(source,value);
    require(result.exit_code==wspq::invalid_arguments,"existing incomplete was accepted");
    require(read_text(incomplete)=="preserve me","existing incomplete was modified");
    require(source.cleanup_calls==0,"source touched during argument refusal");

    FakeClock overflow_clock; ScriptSource overflow_source(overflow_clock,{});
    auto overflow=request(root.path,overflow_clock,"overflow-refusal");
    overflow.sample_count=std::numeric_limits<std::size_t>::max();
    const auto overflow_result=wspq::capture_exact(overflow_source,overflow);
    require(overflow_result.exit_code==wspq::invalid_arguments,"size overflow was accepted");
    require(overflow_source.cleanup_calls==0,"overflow refusal touched source");

    FakeClock utf8_clock; ScriptSource utf8_source(utf8_clock,{});
    auto utf8=request(root.path,utf8_clock,"utf8-refusal");
    utf8.capture_id=std::string("bad")+static_cast<char>(0xff);
    const auto utf8_result=wspq::capture_exact(utf8_source,utf8);
    require(utf8_result.exit_code==wspq::invalid_arguments,"invalid UTF-8 was accepted");
    require(utf8_source.cleanup_calls==0,"UTF-8 refusal touched source");
}

void evidence_write_failure_is_actionable() {
    TestRoot root; FakeClock clock;
    ScriptSource source(clock,{{wspq::ReadKind::samples,1},{wspq::ReadKind::end,0}});
    auto value=request(root.path,clock,"evidence-failure");
    value.failure_metadata_path=root.path/"missing directory"/"capture.failure.json";
    const auto result=wspq::capture_exact(source,value);
    require(result.exit_code==wspq::evidence_failed,"evidence failure exit code is wrong");
    require(result.primary_failure_cause=="short_read","evidence failure erased primary cause");
    require(!result.evidence_error.empty(),"evidence failure is not actionable");
    require(!std::filesystem::exists(value.output_path),"evidence failure left final IQ");

    FakeClock cleanup_clock;
    ScriptSource cleanup_source(cleanup_clock,{{wspq::ReadKind::samples,1},{wspq::ReadKind::end,0}});
    cleanup_source.cleanup_report={{"release"},{"release"}};
    auto cleanup_value=request(root.path,cleanup_clock,"cleanup-evidence-failure");
    cleanup_value.failure_metadata_path=root.path/"another missing directory"/"failure.json";
    const auto cleanup_result=wspq::capture_exact(cleanup_source,cleanup_value);
    require(cleanup_result.exit_code==wspq::cleanup_failed,
            "evidence failure overrode cleanup precedence");
    require(cleanup_result.primary_failure_cause=="short_read",
            "combined cleanup/evidence failure erased primary cause");
}

}  // namespace

int main() {
    try {
        success_exact_partial_and_discard();
        golden_little_endian_cf32();
        paths_with_spaces_and_deterministic_hash();
        failure_contracts();
        deadline_checkpoints();
        cleanup_sequencing();
        cleanup_precedence();
        preserve_existing_incomplete();
        evidence_write_failure_is_actionable();
        std::cout << "hardware-free capture contract tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

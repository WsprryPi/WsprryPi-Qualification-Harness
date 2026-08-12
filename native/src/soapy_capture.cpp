#include "wsprrypi_capture/capture.hpp"

#include <SoapySDR/Device.hpp>
#include <SoapySDR/Errors.hpp>
#include <SoapySDR/Formats.hpp>

#include <complex>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {

class SoapySource final : public wspq::SampleSource, private wspq::CleanupActions {
public:
    explicit SoapySource(long read_timeout_us) : read_timeout_us_(read_timeout_us) {}
    ~SoapySource() override { (void)cleanup(); }

    wspq::Settings configure(const wspq::Settings& requested) override {
        SoapySDR::Kwargs selector;
        selector["driver"] = requested.driver;
        if (!requested.serial.empty()) selector["serial"] = requested.serial;
        const auto matches = SoapySDR::Device::enumerate(selector);
        std::vector<wspq::Settings> identities;
        identities.reserve(matches.size());
        for (const auto& match : matches) {
            auto identity = requested;
            const auto driver = match.find("driver");
            const auto serial = match.find("serial");
            identity.driver = driver == match.end() ? "" : driver->second;
            identity.serial = serial == match.end() ? "" : serial->second;
            identities.push_back(identity);
        }
        const auto resolved_identity = wspq::resolve_device_identity(requested, identities);
        const auto& resolved = matches.front();

        device_ = SoapySDR::Device::make(resolved);
        if (device_ == nullptr) throw std::runtime_error("SoapySDR did not open the requested device");
        const auto direction = SOAPY_SDR_RX;
        const auto channel = static_cast<std::size_t>(requested.channel);
        device_->setSampleRate(direction, channel, requested.sample_rate_hz);
        device_->setBandwidth(direction, channel, requested.bandwidth_hz);
        device_->setFrequency(direction, channel, requested.center_frequency_hz);
        device_->setGainMode(direction, channel, requested.agc);
        device_->setGain(direction, channel, requested.gain_db);
        device_->writeSetting("biasT_ctrl", requested.bias_tee ? "true" : "false");
        stream_ = device_->setupStream(direction, SOAPY_SDR_CF32, {channel});
        if (stream_ == nullptr || device_->activateStream(stream_) != 0)
            throw std::runtime_error("SoapySDR could not activate the requested stream");
        active_ = true;

        wspq::Settings actual = requested;
        actual.driver = resolved_identity.driver;
        actual.serial = resolved_identity.serial;
        actual.sample_rate_hz = device_->getSampleRate(direction, channel);
        actual.bandwidth_hz = device_->getBandwidth(direction, channel);
        actual.center_frequency_hz = device_->getFrequency(direction, channel);
        actual.gain_db = device_->getGain(direction, channel);
        actual.agc = device_->getGainMode(direction, channel);
        actual.bias_tee = device_->readSetting("biasT_ctrl") == "true";
        return actual;
    }

    wspq::ReadResult read(std::complex<float>* destination, std::size_t capacity) override {
        void* buffers[] = {destination};
        int flags = 0;
        long long time_ns = 0;
        const auto result =
            device_->readStream(stream_, buffers, capacity, flags, time_ns, read_timeout_us_);
        if (result > 0) return {wspq::ReadKind::samples, static_cast<std::size_t>(result)};
        if (result == SOAPY_SDR_TIMEOUT) return {wspq::ReadKind::timeout, 0};
        if (result == SOAPY_SDR_OVERFLOW) return {wspq::ReadKind::overflow, 0};
        return {wspq::ReadKind::error, 0};
    }

    wspq::CleanupReport cleanup() noexcept override {
        return wspq::run_cleanup_steps(*this);
    }

private:
    bool needs_deactivate() const noexcept override {
        return device_ != nullptr && stream_ != nullptr && active_;
    }
    bool needs_close() const noexcept override { return device_ != nullptr && stream_ != nullptr; }
    bool needs_release() const noexcept override { return device_ != nullptr; }
    void deactivate() override {
        const auto status = device_->deactivateStream(stream_);
        active_ = false;
        if (status != 0) throw std::runtime_error("stream deactivation failed");
    }
    void close() override {
        auto* stream = stream_;
        stream_ = nullptr;
        device_->closeStream(stream);
    }
    void release() override {
        auto* device = device_;
        device_ = nullptr;
        SoapySDR::Device::unmake(device);
    }

    SoapySDR::Device* device_{nullptr};
    SoapySDR::Stream* stream_{nullptr};
    bool active_{false};
    long read_timeout_us_;
};

}  // namespace

int main(int argc, char** argv) {
    if (argc != 17 || std::string(argv[1]) != "--enable-physical-sdr") {
        std::cerr << "usage: wspq-capture-soapy --enable-physical-sdr DRIVER SERIAL CENTER_HZ "
                     "SAMPLE_COUNT GAIN_DB SAMPLE_RATE_HZ BANDWIDTH_HZ CHANNEL AGC BIAS_TEE "
                     "READ_TIMEOUT_US DEADLINE_S OUTPUT METADATA CAPTURE_ID\n";
        return wspq::invalid_arguments;
    }
    try {
        wspq::CaptureRequest request;
        request.requested.driver = argv[2];
        request.requested.serial = argv[3];
        request.requested.center_frequency_hz = std::stod(argv[4]);
        request.sample_count = static_cast<std::size_t>(std::stoull(argv[5]));
        request.requested.gain_db = std::stod(argv[6]);
        request.requested.sample_rate_hz = std::stod(argv[7]);
        request.requested.bandwidth_hz = std::stod(argv[8]);
        request.requested.channel = std::stoi(argv[9]);
        const std::string agc = argv[10];
        const std::string bias_tee = argv[11];
        if ((agc != "true" && agc != "false") || (bias_tee != "true" && bias_tee != "false"))
            throw std::invalid_argument("AGC and BIAS_TEE must be true or false");
        request.requested.agc = agc == "true";
        request.requested.bias_tee = bias_tee == "true";
        request.read_timeout_us = std::stol(argv[12]);
        request.max_elapsed_duration_s = std::stod(argv[13]);
        request.output_path = argv[14];
        request.metadata_path = argv[15];
        request.failure_metadata_path = std::string(argv[15]) + ".failure.json";
        request.capture_id = argv[16];
        SoapySource source(request.read_timeout_us);
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

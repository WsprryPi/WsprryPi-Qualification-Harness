#include <SoapySDR/Device.hpp>
#include <SoapySDR/Formats.hpp>

#include <chrono>
#include <complex>
#include <cstddef>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

int main(int argc, char** argv)
{
    if (argc != 5)
    {
        std::cerr << "usage: fixed_capture CENTER_HZ SAMPLES GAIN_DB OUTPUT\n";
        return 2;
    }
    const double center_hz = std::stod(argv[1]);
    const std::size_t sample_count = std::stoull(argv[2]);
    const double gain_db = std::stod(argv[3]);
    const std::string output_path = argv[4];
    constexpr double sample_rate = 250000.0;

    SoapySDR::Kwargs args;
    args["driver"] = "sdrplay";
    SoapySDR::Device* device = SoapySDR::Device::make(args);
    if (device == nullptr)
        throw std::runtime_error("Could not open local RSP1B.");
    SoapySDR::Stream* stream = nullptr;
    try
    {
        device->setSampleRate(SOAPY_SDR_RX, 0, sample_rate);
        device->setBandwidth(SOAPY_SDR_RX, 0, 200000.0);
        device->setFrequency(SOAPY_SDR_RX, 0, center_hz);
        device->setGainMode(SOAPY_SDR_RX, 0, false);
        device->setGain(SOAPY_SDR_RX, 0, gain_db);
        device->writeSetting("biasT_ctrl", "false");
        stream = device->setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32);
        if (stream == nullptr || device->activateStream(stream) != 0)
            throw std::runtime_error("Could not activate local RSP1B stream.");

        std::vector<std::complex<float>> discard(32768);
        void* discard_buffer = discard.data();
        int flags = 0;
        long long time_ns = 0;
        (void)device->readStream(stream, &discard_buffer, discard.size(),
            flags, time_ns, 2000000);

        const auto started = std::chrono::steady_clock::now();
        std::vector<std::complex<float>> samples(sample_count);
        std::size_t filled = 0;
        while (filled < sample_count)
        {
            void* buffer = samples.data() + filled;
            const int result = device->readStream(stream, &buffer,
                sample_count - filled, flags, time_ns, 2000000);
            if (result <= 0)
                throw std::runtime_error("RSP1B read failed: " +
                    std::to_string(result));
            filled += static_cast<std::size_t>(result);
        }
        const auto finished = std::chrono::steady_clock::now();

        std::ofstream output(output_path, std::ios::binary);
        output.write(reinterpret_cast<const char*>(samples.data()),
            static_cast<std::streamsize>(samples.size() * sizeof(samples[0])));
        if (!output) throw std::runtime_error("Could not write IQ output.");

        const auto ns = [](auto point) {
            return std::chrono::duration_cast<std::chrono::nanoseconds>(
                point.time_since_epoch()).count();
        };
        std::cout << std::fixed << std::setprecision(3)
                  << "center_hz=" << center_hz
                  << " sample_rate=" << sample_rate
                  << " samples=" << sample_count
                  << " agc=" << (device->getGainMode(SOAPY_SDR_RX, 0) ? "on" : "off")
                  << " requested_gain_db=" << gain_db
                  << " actual_gain_db=" << device->getGain(SOAPY_SDR_RX, 0)
                  << " capture_start_monotonic_ns=" << ns(started)
                  << " capture_end_monotonic_ns=" << ns(finished)
                  << " output=" << output_path << '\n';

        device->deactivateStream(stream);
        device->closeStream(stream);
        SoapySDR::Device::unmake(device);
        return 0;
    }
    catch (...)
    {
        if (stream != nullptr)
        {
            (void)device->deactivateStream(stream);
            device->closeStream(stream);
        }
        SoapySDR::Device::unmake(device);
        throw;
    }
}

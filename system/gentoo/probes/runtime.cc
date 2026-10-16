/* SPDX-License-Identifier: GPL-3.0-or-later */
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
int main() {
    std::vector<std::string> values;
    std::thread worker([&] { values.emplace_back("gentoo C++ runtime ok"); });
    worker.join();
    try {
        throw std::runtime_error(values.at(0));
    } catch (const std::exception& error) {
        if (std::string(error.what()) != "gentoo C++ runtime ok") return 1;
        std::cout << error.what() << '\n';
        return !std::cout;
    }
    return 2;
}

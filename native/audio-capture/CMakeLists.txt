cmake_minimum_required(VERSION 3.20)
project(valorant_audio_capture LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(valorant_audio_capture main.cpp)

# UTF-8 源文件（中文注释）编译选项
target_compile_options(valorant_audio_capture PRIVATE /utf-8)

# WASAPI / Core Audio
target_link_libraries(valorant_audio_capture PRIVATE
    ole32
    uuid
    mmdevapi
    avrt
)

# Windows 10 Build 20348+ 需要的头文件在 Windows SDK 中已含 audioclientactivationparams.h
# 确保 Windows SDK 版本正确
if(NOT CMAKE_SYSTEM_VERSION)
    set(CMAKE_SYSTEM_VERSION 10.0.26100.0)
endif()

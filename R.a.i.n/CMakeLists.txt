cmake_minimum_required(VERSION 3.10)
project(RAIN_CognitiveFreedom C)

# Forzamos estándares estrictos de C para evitar sorpresas en memoria
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -Wall -Wextra -Wpedantic")

# Le decimos dónde están los cabezales (.h)
include_directories(include)

# 1. Compilamos el motor R.A.I.N. como una biblioteca estática
add_library(rain_core STATIC
    src/cf_evidence.c
    src/cf_assimilation.c
    src/cf_interpretation.c
    src/cf_expression_gate.c
    src/cf_envelope.c
)

# 2. Compilamos la prueba de autoridad y la enlazamos con el motor
add_executable(test_authority tests/test_authority.c)
target_link_libraries(test_authority rain_core)

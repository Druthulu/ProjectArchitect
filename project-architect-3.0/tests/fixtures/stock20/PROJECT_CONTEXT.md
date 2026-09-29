# TerrainDiffusion7DTD — Project Context & Roadmap

> **Version:** 1.0.0
> **Generated:** 2026-07-13
> **Generation:** Gen1 | **Tech Stack:** C#/.NET (7DTD mod via decompiled Assembly-CSharp.dll + Harmony) · ONNX Runtime .NET (DirectML/CUDA) · Python model reference (PyTorch + infinite-tensor)

## Project Overview

A 7 Days to Die mod that replaces the vanilla Random World Generator terrain with ML-generated terrain via ONNX Runtime.

## Architecture

The mod patches the game engine via Harmony to intercept terrain generation calls and route them through the ONNX model.

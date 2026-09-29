# Ops & Setup — TerrainDiffusion7DTD

> **Evolvable reference (docs/ layer). Last revised 2026-07-13.**

## Version pins

| Component | Version | Notes |
|---|---|---|
| .NET SDK | 10.0.301 | builds the mod via dotnet |
| Python | 3.14.5 | reference parity oracle |
| ONNX Runtime | 1.24.4 | GPU via DirectML EP |

## Environment

- **OS / shell:** Windows 11, PowerShell (primary) + Git Bash
- **Project root:** (local clone)

### Build

    dotnet build src/TerrainDiffusion.Mod/TerrainDiffusion.Mod.csproj

### Test

    dotnet test

## Build / run / test

Run `tools/validate.sh` for the full tiered suite: Tier 0 = `dotnet test`, Tier 1 = harness verbs.

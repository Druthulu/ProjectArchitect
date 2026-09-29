# 7DTD Modding Cookbook

> Techniques for modding 7 Days to Die with Harmony and decompiled source.

## §1 — Loadable 7DTD C#/Harmony mod recipe

Build as a net48 class library with Harmony references and drop into the Mods folder.
Use `InitMod` as the entry point and call `PatchAll`.

## §2 — Namespace collision with the game's global Mod type

Wrap your mod code in a project namespace to avoid colliding with the game's `Mod` class.

## §3 — ONNX Runtime (DirectML GPU) inside 7DTD's Mono process

Load ONNX Runtime with DirectML EP for GPU inference. Pin the native DLL paths to beat stale System32 copies.

# Setup — Example Quest Decompilation

> Evolvable ops reference.

## Version pin summary

| Component | Version |
|---|---|
| GCC | 2.7.2.1 (cc1 only) |
| maspsx | 0.5.1 |
| binutils | 2.25 mipsel |

## The all-in-WSL environment

One ext4 clone at `~/bfm-decomp`; no Windows/WSL split, no second clone.

## Build / extract / verify

    make build BINARY=main
    make verify

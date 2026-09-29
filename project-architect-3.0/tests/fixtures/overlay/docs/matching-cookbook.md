# Matching Cookbook — Example Quest Decompilation

> Techniques for matching PS1 decompilation.

## §1 Idiom catalog (asm pattern to C that produces it)

Common MIPS idioms and the C code that GCC 2.7.2 emits for them.

## §2 Writing matching C (what makes gcc emit X)

Statement ordering, variable scoping, and cast placement that control register allocation.

## §3 When a diff is pure scheduling

If the only difference is instruction order within a basic block, try decomp-permuter.

## §4 Flag/toolchain gotchas

Known compiler-flag interactions and assembler-shim edge cases.

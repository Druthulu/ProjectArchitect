r01: kept ["is_valid_isbn"] dropped [] — prompt asks which function
r02: kept ["14"] dropped [] — prompt asks how many days
r03: kept [] dropped ["register", "add"] — prompt asks for locations and a mechanism, names no function
r04: kept ["InactiveMember", "ShelfError"] dropped ["deactivate"] — prompt asks which exception and which base class; the flag is asked by location
r05: kept ["ValueError", "test_rejects_unknown_format"] dropped ["FORMAT"] — prompt asks which exception and which test; it asks the number, not the constant's name
r06: kept ["search"] dropped [] — prompt asks which method
r07: kept ["LimitReached"] dropped ["MAX_RENEWALS"] — prompt asks which exception; the limit is asked as a count and a location, not a constant name
r08: kept ["1000"] dropped [] — prompt asks the maximum fee (a number)
r09: kept ["2024"] dropped ["clock", "Clock"] — prompt asks on which date the tests start; the date source is asked by location and mechanism
r10: kept ["popular"] dropped [] — prompt asks which function
r11: kept ["_queues"] dropped [] — prompt asks which attribute
r12: kept ["normalize_isbn"] dropped [] — prompt asks which function
r13: kept ["YYYY-MM-DD", "_loan_from_dict"] dropped [] — prompt asks which text format and names the containing function
r14: kept ["loan_fee_cents"] dropped ["returned", "GRACE_DAYS"] — prompt asks which function returns the fee; the date and grace days are asked as meaning and count
r15: kept [] dropped ["checkout", "renew"] — prompt asks call sites only; enclosing-function names are not asked
r16: kept [] dropped ["GRACE_DAYS", "FEE_CAP_CENTS"] — prompt asks a traced path by citation; constant names are not asked; 3.9.7 T7: no key change — grader `_covers` now gives a range the ±2 slack a single line has (r16.alt.md cites the body 7-10 for the def at 5)
r17: kept [] dropped ["available", "checkout", "place_hold", "member_loans", "popular_titles"] — prompt asks call sites only; enclosing-function names are not asked
r18: kept ["Unavailable", "Book"] dropped [] — prompt asks which names each module imports
r19: kept [] dropped ["_next"] — prompt asks how and why; the counter attribute is not asked by name
r20: kept [] dropped ["book.isbn"] — prompt asks a trace by citation; the attribute expression is not asked
r21: kept ["loan_fee_cents", "overdue_report"] dropped [] — prompt asks which functions call it
r22: kept ["place_hold"] dropped ["today"] — prompt asks the desk method a caller uses; the date source is asked by location; 3.9.8 T9: holds.py `place` ref gains to=25 (def 19-25), a cite inside the def covers it; r22.alt.md cites holds.py:23
r23: kept ["check_kind"] dropped [] — prompt asks for the validating function
r24: kept ["loan_id"] dropped [] — prompt asks what decides the order (the sort key)
r25: kept ["NotFound", "Unavailable", "LimitReached", "InactiveMember"] dropped [] — prompt asks which exceptions
r26: kept [] dropped ["available"] — prompt asks what happens and where; the attribute is not asked by name
r27: kept [] dropped ["DAILY_FEE_CENTS"]; ref policy.py:7 → :29 (`def daily_fee`) — the prompt asks where the kind is turned into a rate: the accessor, not the table; alt cites :29-30, alt2 :30 (slack 2)
r28: kept ["M0002", "student", "staff"] dropped [] — prompt asks which members and kinds; 3.9.8 T9: helpers.py `library` ref gains to=51 (def 41-51), a cite inside the def covers it; r28.alt.md cites helpers.py:47-49

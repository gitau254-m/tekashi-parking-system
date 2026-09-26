"""
modules - the six modules from the design document (Section 4).

This file makes the 'modules' folder a Python PACKAGE, so other files can
write "from modules.slots import ...". It can stay almost empty.

Locking rule used by every module:
  - PUBLIC functions (the ones main.py calls, e.g. handle_arrival) take
    state.lock themselves.
  - HELPER functions (e.g. peek_free_slot) do NOT take the lock - they are
    only called from inside a public function that already holds it.
    (threading.Lock cannot be taken twice by the same thread - it would
    wait for itself forever, a "deadlock".)
"""

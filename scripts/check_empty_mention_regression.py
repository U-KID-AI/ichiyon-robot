"""Empty mention regression checks execute the real registry, without bot startup."""
import unittest
from check_message_router import RoutingChecks

if __name__ == '__main__':
    suite = unittest.TestSuite(RoutingChecks(name) for name in (
        'test_empty_mention_db_hit_preempts_panels_and_miss_falls_through',
        'test_registry_order_independent_of_declaration',
    ))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())

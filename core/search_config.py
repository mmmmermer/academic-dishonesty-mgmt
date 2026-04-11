"""
Search configuration defaults.

Keep ranking/threshold/limit values centralized so matching behavior is
configurable and maintainable without scattering magic numbers.
"""

# Query safety limits
SEARCH_INPUT_MAX_LENGTH = 256
SEARCH_TERM_MAX_COUNT = 50
PYTHON_NAME_SCAN_YIELD_BATCH = 500
SEARCH_RESULT_HARD_LIMIT = 200
TEACHER_TERM_CANDIDATE_LIMIT = 300

# Fuzzy search limits
TEACHER_SUGGESTION_LIMIT = 5
ADMIN_EXTENDED_RECALL_LIMIT = 20

# Pinyin thresholds
PINYIN_ABBR_EXACT_MIN_LEN = 2
PINYIN_PREFIX_MIN_LEN = 2
PINYIN_SUBSTRING_MIN_LEN = 2

# Match rank ordering: lower is better
SEARCH_MATCH_RANKS = {
    "student_id_exact": 0,
    "name_exact": 10,
    "name_partial": 20,
    "pinyin_full": 30,
    "pinyin_abbr": 40,
    "pinyin_char_exact": 45,
    "pinyin_prefix": 50,
    "pinyin_abbr_prefix": 60,
    "pinyin_substring": 70,
}

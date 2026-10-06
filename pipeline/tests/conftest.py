# --- assemblee connector markers (sic.sources.assemblee) ---
def pytest_configure(config):
    config.addinivalue_line("markers", "network: test needs network access (real open-data downloads/APIs)")
    config.addinivalue_line("markers", "index: test needs a locally built real data index (skipped when missing)")

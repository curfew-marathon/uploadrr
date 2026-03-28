import time
from uploadrr.config import Config


def run_benchmark():
    config = Config()

    # We will simulate a lot of lookups.
    # Let's mock data by extending the self.data
    for i in range(1000):
        config.data.append({"archive": f"/archives/mock_{i}", "serial": f"serial_{i}"})
        if hasattr(config, "_archive_to_serial"):
            config._archive_to_serial[f"/archives/mock_{i}"] = f"serial_{i}"

    start_time = time.perf_counter()
    for _ in range(100):
        for i in range(1000):
            try:
                config.get_serial(f"/archives/mock_{i}")
            except KeyError:
                pass
    end_time = time.perf_counter()
    print(f"Lookups took {end_time - start_time:.6f} seconds")


if __name__ == "__main__":
    run_benchmark()

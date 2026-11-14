# Optional shortcuts for macOS/Linux. Everything goes through run.py,
# which works the same on Windows:  python run.py <command>
.PHONY: setup doctor demo tunnel run sim replay mic listen calls test run-dry
setup doctor demo tunnel run sim replay mic listen calls test:
	python3 run.py $@
run-dry:
	python3 run.py run --dry

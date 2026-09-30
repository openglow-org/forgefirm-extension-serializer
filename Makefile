# Copyright 2026 514 LLC d/b/a OpenGlow
# Written by Scott Wiederhold
# SPDX-License-Identifier: MIT
#
# org.openglow.serializer: a page, and the service that keeps the profiles,
# the counters, and the run (bin/run.py and lib/serializer, Python, with
# the SDK's lib/ffx.py). `make test` runs the service's tests, and
# `make test-page` runs the page through a whole run in a headless browser
# (tests/harness.py). `make stage` lays out in build/pkg what the package
# ships - the manifest, ui, bin, lib, and share, and nothing of the
# repository's own files - `make lint` judges it as the machine does, and
# `make pack` packs it (KEY=<file.priv> signs it; its .pub must be beside
# it). FFX is forgeext's tools/ffx: a sibling checkout by default, and the
# kit's in the shared workflow.

FFX    ?= ../forgeext/tools/ffx
PYTHON ?= python3
SHIP    = manifest.json $(wildcard ui bin lib share)

test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) tests/engine_test.py
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) tests/runner_test.py
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) tests/api_test.py

test-page:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) tests/harness.py --scenario
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) tests/harness.py --scenario --units imperial

stage: manifest.json
	rm -rf build/pkg
	mkdir -p build/pkg
	cp -R $(SHIP) build/pkg/
	find build/pkg -name __pycache__ -prune -exec rm -rf {} +

lint: stage
	$(PYTHON) $(FFX) lint build/pkg

pack: stage
	$(PYTHON) $(FFX) pack build/pkg $(if $(KEY),--key $(KEY)) --out build/org.openglow.serializer.ffx

clean:
	rm -rf build

.PHONY: test test-page stage lint pack clean

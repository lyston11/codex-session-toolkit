VENV_DIR ?= .venv
VENV_PYTHON := $(VENV_DIR)/bin/python
PYTHON ?= $(if $(wildcard $(VENV_PYTHON)),$(VENV_PYTHON),python3)
PIP ?= $(PYTHON) -m pip
PACKAGE_MODULE := codex_session_toolkit
PACKAGE_COMMAND := codex-session-toolkit
PY_CACHE_DIR := $(CURDIR)/.pycache
DEV_PIP_PACKAGES := 'ruff>=0.6,<1.0'

.PHONY: help run install install-dev bootstrap bootstrap-editable release test test-quick lint compile ci check version smoke

help:
	@printf "%s\n" \
	"make bootstrap - create .venv and install the toolkit locally" \
	"make bootstrap-editable - create .venv and install in editable mode" \
	"make install-dev - create/update isolated .venv and add development extras" \
	"make release  - build distributable release archives under dist/releases" \
	"make run      - run the toolkit, preferring the local .venv interpreter" \
	"make install  - alias of bootstrap-editable (keeps installs inside .venv)" \
	"make version  - print packaged command version from the local environment" \
	"make compile  - byte-compile package modules with the local environment when available" \
	"make lint     - run Ruff against src and tests inside the local environment when available" \
	"make test-quick - run default unittest discovery inside the local environment when available" \
	"make test     - run packaging/CLI smoke tests inside the local environment when available" \
	"make smoke    - run launcher/module help smoke checks" \
	"make ci       - run compile + lint + tests + smoke" \
	"make check    - alias of make ci"

run:
	sh ./codex-session-toolkit

bootstrap:
	sh ./install.sh

bootstrap-editable:
	sh ./install.sh --editable

release:
	sh ./release.sh

install: bootstrap-editable

install-dev:
	sh ./install.sh --editable
	$(VENV_PYTHON) -m pip install $(DEV_PIP_PACKAGES)

version:
	PYTHONPYCACHEPREFIX=$(PY_CACHE_DIR) PYTHONPATH=src $(PYTHON) -m $(PACKAGE_MODULE) --version

compile:
	PYTHONPYCACHEPREFIX=$(PY_CACHE_DIR) $(PYTHON) -m compileall -q src tests

lint:
	PYTHONPYCACHEPREFIX=$(PY_CACHE_DIR) PYTHONPATH=src $(PYTHON) -m ruff check src tests

test-quick:
	PYTHONPYCACHEPREFIX=$(PY_CACHE_DIR) PYTHONPATH=src $(PYTHON) -m unittest -q

test:
	PYTHONPYCACHEPREFIX=$(PY_CACHE_DIR) PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

smoke:
	sh ./install.sh --help >/dev/null
	sh ./release.sh --help >/dev/null
	sh ./codex-session-toolkit --help >/dev/null
	sh ./scripts/compat/cst-launcher.sh --help >/dev/null
	PYTHONPYCACHEPREFIX=$(PY_CACHE_DIR) PYTHONPATH=src $(PYTHON) -m $(PACKAGE_MODULE) --help >/dev/null

ci: compile lint test smoke

check: ci

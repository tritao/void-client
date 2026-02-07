SHELL := bash

SRC_DIR ?= client/src
BUILD_DIR ?= build
CLASSES_DIR ?= $(BUILD_DIR)/classes
SOURCES_FILE ?= $(BUILD_DIR)/sources.txt
CLASSES_CSV ?= client/refactor/.refactor-plan/generated/classes.csv
SYMBOLS_CSV ?= client/refactor/.refactor-plan/generated/symbol_renames.csv
SYMBOLS_CSV_DIR ?= client/refactor/.refactor-plan/symbol-renames/generated
LSP_TIMEOUT_S ?= 180

LIBS ?= libs/clientlibs.jar
MAIN_CLASS ?= Loader
OUT_JAR ?= $(BUILD_DIR)/void-client.jar
CLASSES_STAMP ?= $(CLASSES_DIR)/.compiled.stamp
JAVA_ARGS ?=
EXCLUDE_REGEX ?=

# Requested JDK major version. Used to validate JAVA_HOME and auto-select a bootstrapped JDK under ./.jdk/.
JDK ?= 8
BOOTSTRAP_JAVA_HOME ?= $(CURDIR)/.jdk/temurin$(JDK)

# JDTLS now commonly requires a newer runtime than the client (which targets Java 8).
JDTLS_JDK ?= 21
JDTLS_JAVA_HOME ?= $(CURDIR)/.jdk/temurin$(JDTLS_JDK)
ifeq ($(wildcard $(JDTLS_JAVA_HOME)/bin/java),)
JDTLS_JAVA ?= java
else
JDTLS_JAVA ?= $(JDTLS_JAVA_HOME)/bin/java
endif

# If JAVA_HOME isn't set (or doesn't match JDK), fall back to a repo-local bootstrapped JDK if present.
JAVA_HOME_BIN_JAVA := $(JAVA_HOME)/bin/java
JAVA_HOME_BIN_JAVAC := $(JAVA_HOME)/bin/javac

ifdef JAVA_HOME
  ifeq ($(wildcard $(JAVA_HOME_BIN_JAVA)),)
    ifneq ($(wildcard $(BOOTSTRAP_JAVA_HOME)/bin/java),)
      $(warning JAVA_HOME is set but invalid ($(JAVA_HOME_BIN_JAVA) missing); using $(BOOTSTRAP_JAVA_HOME))
      JAVA_HOME := $(BOOTSTRAP_JAVA_HOME)
    else
      $(warning JAVA_HOME is set but invalid ($(JAVA_HOME_BIN_JAVA) missing); falling back to PATH)
    endif
  else
    JAVA_HOME_MAJOR := $(shell "$(JAVA_HOME_BIN_JAVA)" -version 2>&1 | sed -n '1{s/.*version \"1\.\([0-9][0-9]*\).*/\1/p; s/.*version \"\([0-9][0-9]*\).*/\1/p;}')
    ifneq ($(JAVA_HOME_MAJOR),$(JDK))
      ifneq ($(wildcard $(BOOTSTRAP_JAVA_HOME)/bin/java),)
        $(warning JAVA_HOME is Java $(JAVA_HOME_MAJOR) but JDK=$(JDK); using $(BOOTSTRAP_JAVA_HOME))
        JAVA_HOME := $(BOOTSTRAP_JAVA_HOME)
      else
        $(warning JAVA_HOME is Java $(JAVA_HOME_MAJOR) but JDK=$(JDK); continuing with JAVA_HOME)
      endif
    endif
  endif
else
  ifneq ($(wildcard $(BOOTSTRAP_JAVA_HOME)/bin/java),)
    JAVA_HOME := $(BOOTSTRAP_JAVA_HOME)
  endif
endif

ifdef JAVA_HOME
JAVA := $(JAVA_HOME)/bin/java
JAVAC := $(JAVA_HOME)/bin/javac
JAR := $(JAVA_HOME)/bin/jar
else
JAVA ?= java
JAVAC ?= javac
JAR ?= jar
endif

.PHONY: help bootstrap bootstrap-jdtls bootstrap-jdtls-jdk sources sources-recursive compile compile-recursive jar run clean reports compile-refactor rebuild-refactor
.PHONY: bootstrap-refactor-tools rename-refactor rename-refactor-dry rename-refactor-loop
.PHONY: reports-refactor
.PHONY: static-split-candidates extract-statics extract-statics-dry check-static-extract extract-statics-loop
.PHONY: refactor-loop refactor-loop-dry
.PHONY: build-refactor-views migrate-refactor-plan cleanup-refactor
.PHONY: cleanup-candidates cleanup-apply-high cleanup-report

help:
	@echo "Targets:"
	@echo "  make bootstrap - download repo-local JDK (./.jdk/temurin\$$JDK)"
	@echo "  make bootstrap-jdtls - download repo-local JDTLS (./.jdtls/)"
	@echo "  make bootstrap-jdtls-jdk - download repo-local JDK for JDTLS (./.jdk/temurin\$$JDTLS_JDK)"
	@echo "  make bootstrap-refactor-tools - create ./.venv with refactor tool deps"
	@echo "  make sources   - write $(SOURCES_FILE)"
	@echo "  make sources-recursive - write $(SOURCES_FILE) (recursive)"
	@echo "  make compile   - compile $(SRC_DIR) into $(CLASSES_DIR)"
	@echo "  make compile-recursive - compile $(SRC_DIR) into $(CLASSES_DIR) (recursive)"
	@echo "  make jar       - build runnable jar at $(OUT_JAR) (Main-Class: $(MAIN_CLASS))"
	@echo "  make run       - run $(MAIN_CLASS) using $(OUT_JAR) + $(LIBS)"
	@echo "  make reports   - regenerate docs/*.md reports"
	@echo "  make rebuild-refactor - full refactor rebuild (layout + views + static extraction + validation)"
	@echo "  make compile-refactor - compile client/refactor (after rebuild-refactor)"
	@echo "  make rename-refactor - apply symbol renames in client/refactor"
	@echo "  make rename-refactor-dry - dry-run rename-refactor"
	@echo "  make rename-refactor-loop - rebuild-refactor + rename-refactor + compile + reports"
	@echo "  make static-split-candidates - rank static members for extraction manifests"
	@echo "  make extract-statics - apply client/refactor/.refactor-plan/extract-statics/generated/*.yaml"
	@echo "  make extract-statics-dry - dry-run static extraction"
	@echo "  make check-static-extract - validate manifests are fully applied"
	@echo "  make extract-statics-loop - extract + validate + compile client/refactor"
	@echo "  make refactor-loop - unified class+symbol+extract+validate+compile pipeline"
	@echo "  make refactor-loop-dry - dry-run unified pipeline"
	@echo "  make build-refactor-views - build generated symbol/extract/class views from .refactor-plan"
	@echo "  make migrate-refactor-plan - import existing symbol/extract artifacts into .refactor-plan"
	@echo "  make cleanup-refactor - apply post-rebuild cleanup pass in client/refactor"
	@echo "  make cleanup-candidates - detect high-confidence cleanup candidates and write reports"
	@echo "  make cleanup-apply-high - merge high-confidence candidates into drop_members.csv"
	@echo "  make cleanup-report - regenerate docs/cleanup-candidates.md from detector output"
	@echo "  make clean     - remove $(BUILD_DIR)"
	@echo ""
	@echo "Vars:"
	@echo "  JDK=8                    (requested major version; default 8)"
	@echo "  JAVA_HOME=/path/to/jdk   (used if compatible with JDK; otherwise ./.jdk/temurin\$$JDK is preferred)"
	@echo "  LIBS=libs/clientlibs.jar (classpath deps)"
	@echo "  EXCLUDE_REGEX=regex      (exclude source paths matching regex; useful for platform-specific files)"
	@echo "  MAX_RENAMES=20           (cap renames per run for rename/rename-loop)"
	@echo ""
	@echo "Tip:"
	@echo "  tools/bootstrap-jdk.sh \$$JDK  (downloads a repo-local JDK into .jdk/)"

bootstrap:
	@echo "Bootstrapping Temurin JDK $(JDK) into $(BOOTSTRAP_JAVA_HOME)"
	@bash tools/bootstrap-jdk.sh "$(JDK)"

bootstrap-jdtls:
	@bash tools/bootstrap-jdtls.sh

bootstrap-jdtls-jdk:
	@echo "Bootstrapping Temurin JDK $(JDTLS_JDK) into $(JDTLS_JAVA_HOME)"
	@bash tools/bootstrap-jdk.sh "$(JDTLS_JDK)"

bootstrap-refactor-tools:
	@bash tools/bootstrap-refactor-tools.sh

JAVA_SOURCES := $(wildcard $(SRC_DIR)/*.java)
LIB_JARS := $(subst :, ,$(LIBS))

$(BUILD_DIR):
	@mkdir -p "$@"

$(CLASSES_DIR):
	@mkdir -p "$@"

sources: $(BUILD_DIR)
	@find "$(SRC_DIR)" -maxdepth 1 -name '*.java' -print | sort > "$(SOURCES_FILE)"
	@if [ -n "$(EXCLUDE_REGEX)" ]; then grep -Ev "$(EXCLUDE_REGEX)" "$(SOURCES_FILE)" > "$(SOURCES_FILE).tmp" || true; mv "$(SOURCES_FILE).tmp" "$(SOURCES_FILE)"; fi
	@echo "Wrote $(SOURCES_FILE) ($$(wc -l < "$(SOURCES_FILE)") files)"

sources-recursive: $(BUILD_DIR)
	@find "$(SRC_DIR)" -name '*.java' -print | sort > "$(SOURCES_FILE)"
	@if [ -n "$(EXCLUDE_REGEX)" ]; then grep -Ev "$(EXCLUDE_REGEX)" "$(SOURCES_FILE)" > "$(SOURCES_FILE).tmp" || true; mv "$(SOURCES_FILE).tmp" "$(SOURCES_FILE)"; fi
	@echo "Wrote $(SOURCES_FILE) ($$(wc -l < "$(SOURCES_FILE)") files)"

$(CLASSES_STAMP): $(JAVA_SOURCES) $(LIB_JARS) | $(CLASSES_DIR) $(BUILD_DIR)
	@echo "Compiling with: $(JAVAC)"
	@find "$(SRC_DIR)" -maxdepth 1 -name '*.java' -print | sort > "$(SOURCES_FILE)"
	@if [ -n "$(EXCLUDE_REGEX)" ]; then grep -Ev "$(EXCLUDE_REGEX)" "$(SOURCES_FILE)" > "$(SOURCES_FILE).tmp" || true; mv "$(SOURCES_FILE).tmp" "$(SOURCES_FILE)"; fi
	@"$(JAVAC)" -Xlint:none -cp "$(LIBS)" -d "$(CLASSES_DIR)" @"$(SOURCES_FILE)"
	@touch "$(CLASSES_STAMP)"

compile: $(CLASSES_STAMP)

CLASSES_STAMP_RECURSIVE ?= $(CLASSES_DIR)/.compiled-recursive.stamp
JAVA_SOURCES_RECURSIVE := $(shell find "$(SRC_DIR)" -name '*.java' -print)

$(CLASSES_STAMP_RECURSIVE): $(JAVA_SOURCES_RECURSIVE) $(LIB_JARS) | $(CLASSES_DIR) $(BUILD_DIR)
	@echo "Compiling (recursive) with: $(JAVAC)"
	@find "$(SRC_DIR)" -name '*.java' -print | sort > "$(SOURCES_FILE)"
	@if [ -n "$(EXCLUDE_REGEX)" ]; then grep -Ev "$(EXCLUDE_REGEX)" "$(SOURCES_FILE)" > "$(SOURCES_FILE).tmp" || true; mv "$(SOURCES_FILE).tmp" "$(SOURCES_FILE)"; fi
	@"$(JAVAC)" -Xlint:none -cp "$(LIBS)" -d "$(CLASSES_DIR)" @"$(SOURCES_FILE)"
	@touch "$(CLASSES_STAMP_RECURSIVE)"

compile-recursive: $(CLASSES_STAMP_RECURSIVE)

$(OUT_JAR): $(CLASSES_STAMP) | $(BUILD_DIR)
	@echo "Jarring to: $(OUT_JAR)"
	@"$(JAR)" cfe "$(OUT_JAR)" "$(MAIN_CLASS)" -C "$(CLASSES_DIR)" .

jar: $(OUT_JAR)

run: $(OUT_JAR)
	@"$(JAVA)" $(JAVA_ARGS) -cp "$(OUT_JAR):$(LIBS)" "$(MAIN_CLASS)"

reports:
	@python tools/unnamed_report.py --write docs/unnamed-status.md
	@python tools/fan_graph.py --write docs/fan-graph.md
	@python tools/java_dossier.py --write docs/rename-dossiers.md

reports-refactor:
	@python tools/unnamed_report.py --root client/refactor --write docs/unnamed-status-refactor.md
	@python tools/fan_graph.py --root client/refactor --all --write docs/fan-graph-refactor.md
	@python tools/java_dossier.py --root client/refactor --all --write docs/rename-dossiers-refactor.md

rename-refactor:
	@./.venv/bin/python tools/ts_rename_identifiers.py --csv "$(SYMBOLS_CSV)" --csv-dir "$(SYMBOLS_CSV_DIR)" --src-dir client/refactor --extract-manifest-dir client/refactor/.refactor-plan/extract-statics/generated --report docs/rename-report-refactor.md --max-mappings "$${MAX_RENAMES:-20}" --safe-preflight

rename-refactor-dry:
	@./.venv/bin/python tools/ts_rename_identifiers.py --csv "$(SYMBOLS_CSV)" --csv-dir "$(SYMBOLS_CSV_DIR)" --src-dir client/refactor --extract-manifest-dir client/refactor/.refactor-plan/extract-statics/generated --report docs/rename-report-refactor.md --max-mappings "$${MAX_RENAMES:-20}" --safe-preflight --dry-run

rename-refactor-loop:
	@$(MAKE) rebuild-refactor
	@$(MAKE) rename-refactor
	@$(MAKE) compile-recursive SRC_DIR=client/refactor CLASSES_DIR=build/classes-refactor-layout SOURCES_FILE=build/sources-refactor-layout.txt
	@$(MAKE) reports-refactor
	@echo "Done. See:"
	@echo "  docs/rename-report-refactor.md"
	@echo "  build/refactor-layout-report.md"
	@echo "  docs/unnamed-status-refactor.md"
	@echo "  docs/fan-graph-refactor.md"
	@echo "  docs/rename-dossiers-refactor.md"

rebuild-refactor:
	@python tools/build_refactor_layout.py --csv "$(CLASSES_CSV)" --src-dir client/src --dst-dir client/refactor --rules client/refactor/.refactor-plan/layout_rules.csv --report build/refactor-layout-report.md --rename-report build/refactor-layout-rename-report.md
	@$(MAKE) build-refactor-views
	@$(MAKE) extract-statics
	@$(MAKE) check-static-extract
	@MAX_RENAMES=-1 $(MAKE) rename-refactor
	@$(MAKE) cleanup-refactor

cleanup-refactor:
	@./.venv/bin/python tools/apply_refactor_cleanup.py --src-dir client/refactor --plan-dir client/refactor/.refactor-plan

cleanup-candidates:
	@./.venv/bin/python tools/find_cleanup_candidates.py --src-dir client/refactor --out-csv client/refactor/.refactor-plan/generated/cleanup_candidates.csv --out-md docs/cleanup-candidates.md

cleanup-apply-high:
	@./.venv/bin/python tools/apply_cleanup_candidates.py --candidates client/refactor/.refactor-plan/generated/cleanup_candidates.csv --drop-members client/refactor/.refactor-plan/drop_members.csv --confidence high

cleanup-report:
	@./.venv/bin/python tools/find_cleanup_candidates.py --src-dir client/refactor --out-csv client/refactor/.refactor-plan/generated/cleanup_candidates.csv --out-md docs/cleanup-candidates.md

compile-refactor: rebuild-refactor
	@$(MAKE) compile-recursive SRC_DIR=client/refactor CLASSES_DIR=build/classes-refactor-layout SOURCES_FILE=build/sources-refactor-layout.txt

static-split-candidates:
	@./.venv/bin/python tools/static_split_candidates.py --src-dir client/refactor --scope-dir client/refactor/collections --out-md docs/static-split-candidates.md --out-csv docs/static-split-candidates.csv

extract-statics:
	@./.venv/bin/python tools/extract_statics_ts.py --src-dir client/refactor --manifest-dir client/refactor/.refactor-plan/extract-statics/generated --max-manifests "$${MAX_MANIFESTS:--1}"

extract-statics-dry:
	@./.venv/bin/python tools/extract_statics_ts.py --src-dir client/refactor --manifest-dir client/refactor/.refactor-plan/extract-statics/generated --max-manifests "$${MAX_MANIFESTS:--1}" --dry-run

check-static-extract:
	@./.venv/bin/python tools/check_static_extract.py --src-dir client/refactor --manifest-dir client/refactor/.refactor-plan/extract-statics/generated

extract-statics-loop:
	@$(MAKE) extract-statics
	@$(MAKE) check-static-extract
	@$(MAKE) compile-recursive SRC_DIR=client/refactor CLASSES_DIR=build/classes-refactor-layout SOURCES_FILE=build/sources-refactor-layout.txt

refactor-loop:
	@./.venv/bin/python tools/refactor_pipeline.py --max-renames "$${MAX_RENAMES:-20}" --max-manifests "$${MAX_MANIFESTS:-10}" --resume --skip-class-renames $${ALLOW_CONFLICTS:+--allow-conflicts}

refactor-loop-dry:
	@./.venv/bin/python tools/refactor_pipeline.py --max-renames "$${MAX_RENAMES:-20}" --max-manifests "$${MAX_MANIFESTS:-10}" --dry-run --resume --skip-class-renames $${ALLOW_CONFLICTS:+--allow-conflicts}

build-refactor-views:
	@./.venv/bin/python tools/build_refactor_views.py --plan-dir client/refactor/.refactor-plan --refactor-src client/refactor --out-symbol-root client/refactor/.refactor-plan/generated/symbol_renames.csv --out-symbol-dir client/refactor/.refactor-plan/symbol-renames/generated --out-class-csv client/refactor/.refactor-plan/generated/classes.csv --out-extract-dir client/refactor/.refactor-plan/extract-statics/generated --report docs/refactor-views-report.md $${ALLOW_CONFLICTS:+--allow-conflicts}

migrate-refactor-plan:
	@./.venv/bin/python tools/migrate_refactor_plan.py --plan-dir client/refactor/.refactor-plan --symbols-root-csv client/refactor/.refactor-plan/import/symbol_renames.csv --symbols-dir client/refactor/.refactor-plan/import/symbol-renames --extract-dir client/refactor/.refactor-plan/import/extract-statics --clean
clean:
	rm -rf "$(BUILD_DIR)"

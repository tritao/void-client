SHELL := bash

SRC_DIR ?= client/src
BUILD_DIR ?= build
CLASSES_DIR ?= $(BUILD_DIR)/classes
SOURCES_FILE ?= $(BUILD_DIR)/sources.txt
CLASSES_CSV ?= client/refactor/.refactor-plan/generated/classes.csv
SYMBOLS_CSV ?= client/refactor/.refactor-plan/generated/symbol_renames.csv
SYMBOLS_CSV_DIR ?= client/refactor/.refactor-plan/symbol-renames/generated
EXTRACT_TOUCHED_FILES ?= build/refactor-cache/extract-touched-files.txt
LSP_TIMEOUT_S ?= 180
REF_PY ?= ./.venv/bin/python

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

.PHONY: help bootstrap bootstrap-jdtls bootstrap-jdtls-jdk sources sources-recursive compile compile-recursive jar run clean reports compile-refactor compile-refactor-fast rebuild-refactor rebuild-refactor-compile
.PHONY: bootstrap-refactor-tools rename-refactor rename-refactor-dry rename-refactor-loop
.PHONY: reports-refactor
.PHONY: static-split-candidates extract-statics extract-statics-dry check-static-extract extract-statics-loop
.PHONY: refactor-loop refactor-loop-dry
.PHONY: build-refactor-views build-refactor-class-view migrate-refactor-plan cleanup-refactor
.PHONY: cleanup-candidates cleanup-apply-high cleanup-report
.PHONY: build-cleanup-plan preflight-cleanup-refactor
.PHONY: clean-refactor-cache
.PHONY: test-refactor-tools test-refactor-tools-fast test-refactor-tools-full verify-refactor-tooling

PROFILE ?= 0
PROFILE_DIR ?= $(BUILD_DIR)/profile
PROFILE_FILE ?= $(PROFILE_DIR)/refactor-stages.tsv
PROFILE_TIME ?= /usr/bin/time
PROFILE_TIME_FMT ?= %e\t%U\t%S\t%M

define RUN_WITH_PROFILE
@if [ "$${PROFILE:-0}" = "1" ]; then \
	mkdir -p "$(PROFILE_DIR)"; \
	"$(PROFILE_TIME)" -f "$1\t$(PROFILE_TIME_FMT)" -o "$(PROFILE_FILE)" -a $(2); \
else \
	$(2); \
fi
endef

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
	@echo "  make rebuild-refactor - incremental refactor rebuild (layout + views + extract + rename + cleanup)"
	@echo "  make rebuild-refactor-compile - rebuild-refactor + compile client/refactor"
	@echo "  make compile-refactor - compile current client/refactor only"
	@echo "  make compile-refactor-fast - fast ECJ compile check for client/refactor"
	@echo "  make rename-refactor - apply symbol renames in client/refactor (legacy; prefer refactor-loop)"
	@echo "  make rename-refactor-dry - dry-run rename-refactor"
	@echo "  make rename-refactor-loop - compile-refactor + reports-refactor (legacy)"
	@echo "  make static-split-candidates - rank static members for extraction manifests"
	@echo "  make extract-statics - apply client/refactor/.refactor-plan/extract-statics/generated/*.yaml"
	@echo "  make extract-statics-dry - dry-run static extraction"
	@echo "  make check-static-extract - validate manifests are fully applied"
	@echo "  make extract-statics-loop - extract + validate + compile client/refactor"
	@echo "  make refactor-loop - incremental pipeline (select effective renames + extract + rename + compile)"
	@echo "  make refactor-loop-dry - dry-run incremental pipeline"
	@echo "  make build-refactor-class-view - build generated class rename view only"
	@echo "  make build-refactor-views - build generated symbol/extract/class views from .refactor-plan"
	@echo "  make migrate-refactor-plan - import existing symbol/extract artifacts into .refactor-plan"
	@echo "  make cleanup-refactor - apply post-rebuild cleanup pass in client/refactor"
	@echo "  make build-cleanup-plan - generate cleanup plan with owner remaps in generated/"
	@echo "  make preflight-cleanup-refactor - fail-fast validation for generated cleanup plan"
	@echo "  make cleanup-candidates - detect high-confidence cleanup candidates and write reports"
	@echo "  make cleanup-apply-high - merge high-confidence candidates into drop_members.csv"
	@echo "  make cleanup-report - regenerate docs/cleanup-candidates.md from detector output"
	@echo "  make test-refactor-tools-fast - run fast core unit tests for refactor tooling"
	@echo "  make test-refactor-tools-full - run full refactor tooling test suite"
	@echo "  make test-refactor-tools - alias for test-refactor-tools-full"
	@echo "  make verify-refactor-tooling - run fast tests + core dry-run validation loop"
	@echo "  make clean     - remove $(BUILD_DIR)"
	@echo ""
	@echo "Vars:"
	@echo "  JDK=8                    (requested major version; default 8)"
	@echo "  JAVA_HOME=/path/to/jdk   (used if compatible with JDK; otherwise ./.jdk/temurin\$$JDK is preferred)"
	@echo "  LIBS=libs/clientlibs.jar (classpath deps)"
	@echo "  EXCLUDE_REGEX=regex      (exclude source paths matching regex; useful for platform-specific files)"
	@echo "  MAX_RENAMES=20           (cap effective renames per run for refactor-loop)"
	@echo "  RENAME_JOBS=1            (worker threads for ts_rename_identifiers)"
	@echo "  RENAME_RUST_PREFILTER=off (rename prefilter backend: off|auto|build)"
	@echo "  EXTRACT_JOBS=1           (process workers for extract callsite rewrite pass)"
	@echo "  EXTRACT_RUST_CALLSITES=build (extract callsite backend: off|auto|build)"
	@echo "  FAST_VALIDATE=1          (refactor-loop only: skip expensive global extract callsite validation)"
	@echo "  LOOP_FAST_COMPILE=1      (refactor-loop only: use ECJ fast compile check)"
	@echo "  PROFILE=1                (write stage timings to $(PROFILE_FILE) for rebuild-refactor/refactor-loop)"
	@echo ""
	@echo "Tip:"
	@echo "  tools/bootstrap/bootstrap-jdk.sh \$$JDK  (downloads a repo-local JDK into .jdk/)"

bootstrap:
	@echo "Bootstrapping Temurin JDK $(JDK) into $(BOOTSTRAP_JAVA_HOME)"
	@bash tools/bootstrap/bootstrap-jdk.sh "$(JDK)"

bootstrap-jdtls:
	@bash tools/bootstrap/bootstrap-jdtls.sh

bootstrap-jdtls-jdk:
	@echo "Bootstrapping Temurin JDK $(JDTLS_JDK) into $(JDTLS_JAVA_HOME)"
	@bash tools/bootstrap/bootstrap-jdk.sh "$(JDTLS_JDK)"

bootstrap-refactor-tools:
	@bash tools/bootstrap/bootstrap-refactor-tools.sh

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
ECJ_JAR ?= $(firstword $(wildcard .jdtls/plugins/org.eclipse.jdt.core.compiler.batch_*.jar))
ECJ_CLASSES_DIR ?= $(BUILD_DIR)/classes-refactor-ecj
ECJ_SOURCES_FILE ?= $(BUILD_DIR)/sources-refactor-ecj.txt
ECJ_JAVA ?= $(JDTLS_JAVA)
ECJ_BOOTCLASSPATH ?= $(BOOTSTRAP_JAVA_HOME)/jre/lib/rt.jar

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
	@python tools/reporting/unnamed_report.py --write docs/unnamed-status.md
	@python tools/reporting/fan_graph.py --write docs/fan-graph.md
	@python tools/reporting/java_dossier.py --write docs/rename-dossiers.md

reports-refactor:
	@python tools/reporting/unnamed_report.py --root client/refactor --write docs/unnamed-status-refactor.md
	@python tools/reporting/fan_graph.py --root client/refactor --all --write docs/fan-graph-refactor.md
	@python tools/reporting/java_dossier.py --root client/refactor --all --write docs/rename-dossiers-refactor.md

rename-refactor:
	@$(REF_PY) -m tools.refactor.cli.ts_rename_identifiers --csv "$(SYMBOLS_CSV)" --csv-dir "$(SYMBOLS_CSV_DIR)" --src-dir client/refactor --extract-manifest-dir client/refactor/.refactor-plan/extract-statics/generated --report docs/rename-report-refactor.md --max-mappings "$${MAX_RENAMES:-20}" --jobs "$${RENAME_JOBS:-1}" --rust-token-prefilter "$${RENAME_RUST_PREFILTER:-off}" --safe-preflight

rename-refactor-dry:
	@$(REF_PY) -m tools.refactor.cli.ts_rename_identifiers --csv "$(SYMBOLS_CSV)" --csv-dir "$(SYMBOLS_CSV_DIR)" --src-dir client/refactor --extract-manifest-dir client/refactor/.refactor-plan/extract-statics/generated --report docs/rename-report-refactor.md --max-mappings "$${MAX_RENAMES:-20}" --jobs "$${RENAME_JOBS:-1}" --rust-token-prefilter "$${RENAME_RUST_PREFILTER:-off}" --safe-preflight --dry-run

rename-refactor-loop:
	@$(MAKE) rebuild-refactor-compile
	@$(MAKE) reports-refactor
	@echo "Done. See:"
	@echo "  docs/rename-report-refactor.md"
	@echo "  build/refactor-layout-report.md"
	@echo "  docs/unnamed-status-refactor.md"
	@echo "  docs/fan-graph-refactor.md"
	@echo "  docs/rename-dossiers-refactor.md"

rebuild-refactor:
	@if [ "$${PROFILE:-0}" = "1" ]; then \
		mkdir -p "$(PROFILE_DIR)"; \
		printf 'stage\treal_s\tuser_s\tsys_s\tmaxrss_kb\n' > "$(PROFILE_FILE)"; \
	fi
	@$(MAKE) "$(REFACTOR_CLEANUP_STAMP)"
	@if [ "$${PROFILE:-0}" = "1" ]; then \
		echo "Profile written: $(PROFILE_FILE)"; \
	fi

REFACTOR_STAMP_DIR ?= $(BUILD_DIR)/refactor-stamps
REFACTOR_LAYOUT_STAMP := $(REFACTOR_STAMP_DIR)/layout.stamp
REFACTOR_VIEWS_STAMP := $(REFACTOR_STAMP_DIR)/views.stamp
REFACTOR_EXTRACT_STAMP := $(REFACTOR_STAMP_DIR)/extract.stamp
REFACTOR_CHECK_STAMP := $(REFACTOR_STAMP_DIR)/check_extract.stamp
REFACTOR_RENAME_STAMP := $(REFACTOR_STAMP_DIR)/rename.stamp
REFACTOR_CLEANUP_PLAN_STAMP := $(REFACTOR_STAMP_DIR)/cleanup_plan.stamp
REFACTOR_CLEANUP_STAMP := $(REFACTOR_STAMP_DIR)/cleanup.stamp
REFACTOR_CLEANUP_PLAN_DIR := client/refactor/.refactor-plan/generated/cleanup-plan
REFACTOR_PLAN_INPUTS := $(shell find client/refactor/.refactor-plan -type f \( -name '*.csv' -o -name '*.yaml' \) ! -path '*/generated/*' ! -path '*/import/*')
REFACTOR_CLASS_PLAN_INPUTS := $(shell find client/refactor/.refactor-plan -type f -name '*.class_rename.csv' ! -path '*/generated/*' ! -path '*/import/*')
REFACTOR_SRC_SOURCES := $(shell find client/src -name '*.java' -print)

$(REFACTOR_STAMP_DIR):
	@mkdir -p "$@"

$(REFACTOR_LAYOUT_STAMP): tools/refactor/orchestration/build_refactor_layout.py tools/refactor/cli/build_refactor_views.py client/refactor/.refactor-plan/layout_rules.csv $(REFACTOR_CLASS_PLAN_INPUTS) $(REFACTOR_SRC_SOURCES) Makefile | $(REFACTOR_STAMP_DIR)
	$(call RUN_WITH_PROFILE,build_refactor_class_view,$(MAKE) build-refactor-class-view)
	$(call RUN_WITH_PROFILE,build_refactor_layout,python -m tools.refactor.orchestration.build_refactor_layout --csv "$(CLASSES_CSV)" --src-dir client/src --dst-dir client/refactor --rules client/refactor/.refactor-plan/layout_rules.csv --report build/refactor-layout-report.md --rename-report build/refactor-layout-rename-report.md)
	@touch "$@"

$(REFACTOR_VIEWS_STAMP): tools/refactor/cli/build_refactor_views.py $(REFACTOR_LAYOUT_STAMP) $(REFACTOR_PLAN_INPUTS) Makefile | $(REFACTOR_STAMP_DIR)
	$(call RUN_WITH_PROFILE,build_refactor_views,$(MAKE) build-refactor-views)
	@touch "$@"

$(REFACTOR_EXTRACT_STAMP): tools/refactor/cli/extract_statics_ts.py $(REFACTOR_VIEWS_STAMP) Makefile | $(REFACTOR_STAMP_DIR)
	$(call RUN_WITH_PROFILE,extract_statics,$(MAKE) extract-statics)
	@touch "$@"

$(REFACTOR_CHECK_STAMP): tools/refactor/cli/check_static_extract.py $(REFACTOR_EXTRACT_STAMP) Makefile | $(REFACTOR_STAMP_DIR)
	$(call RUN_WITH_PROFILE,check_static_extract,$(MAKE) check-static-extract)
	@touch "$@"

$(REFACTOR_RENAME_STAMP): tools/refactor/cli/ts_rename_identifiers.py $(REFACTOR_CHECK_STAMP) Makefile | $(REFACTOR_STAMP_DIR)
	$(call RUN_WITH_PROFILE,rename_refactor,env MAX_RENAMES=-1 $(MAKE) rename-refactor)
	@touch "$@"

$(REFACTOR_CLEANUP_PLAN_STAMP): tools/refactor/cli/remap_cleanup_owners.py tools/refactor/cli/preflight_refactor_cleanup.py $(REFACTOR_RENAME_STAMP) Makefile | $(REFACTOR_STAMP_DIR)
	$(call RUN_WITH_PROFILE,build_cleanup_plan,$(MAKE) build-cleanup-plan)
	$(call RUN_WITH_PROFILE,preflight_cleanup_refactor,$(MAKE) preflight-cleanup-refactor)
	@touch "$@"

$(REFACTOR_CLEANUP_STAMP): tools/refactor/cli/apply_refactor_cleanup.py $(REFACTOR_CLEANUP_PLAN_STAMP) Makefile | $(REFACTOR_STAMP_DIR)
	$(call RUN_WITH_PROFILE,cleanup_refactor,$(MAKE) cleanup-refactor)
	@touch "$@"

cleanup-refactor:
	@$(REF_PY) -m tools.refactor.cli.apply_refactor_cleanup --src-dir client/refactor --plan-dir "$(REFACTOR_CLEANUP_PLAN_DIR)"

build-cleanup-plan:
	@$(REF_PY) -m tools.refactor.cli.remap_cleanup_owners --plan-dir client/refactor/.refactor-plan --manifest-dir client/refactor/.refactor-plan/extract-statics/generated --out-dir "$(REFACTOR_CLEANUP_PLAN_DIR)" --fail-on-warnings

preflight-cleanup-refactor:
	@$(REF_PY) -m tools.refactor.cli.preflight_refactor_cleanup --src-dir client/refactor --plan-dir "$(REFACTOR_CLEANUP_PLAN_DIR)"

cleanup-candidates:
	@$(REF_PY) -m tools.refactor.cleanup.find_cleanup_candidates --src-dir client/refactor --out-csv client/refactor/.refactor-plan/generated/cleanup_candidates.csv --out-md docs/cleanup-candidates.md

cleanup-apply-high:
	@$(REF_PY) -m tools.refactor.cleanup.apply_cleanup_candidates --candidates client/refactor/.refactor-plan/generated/cleanup_candidates.csv --drop-members client/refactor/.refactor-plan/drop_members.csv --confidence high

cleanup-report:
	@$(REF_PY) -m tools.refactor.cleanup.find_cleanup_candidates --src-dir client/refactor --out-csv client/refactor/.refactor-plan/generated/cleanup_candidates.csv --out-md docs/cleanup-candidates.md

compile-refactor:
	@$(MAKE) compile-recursive SRC_DIR=client/refactor CLASSES_DIR=build/classes-refactor-layout SOURCES_FILE=build/sources-refactor-layout.txt

compile-refactor-fast:
	@if [ -z "$(ECJ_JAR)" ]; then echo "Missing ECJ jar in .jdtls/plugins (run: make bootstrap-jdtls)"; exit 2; fi
	@if [ ! -f "$(ECJ_BOOTCLASSPATH)" ]; then echo "Missing Java 8 rt.jar at $(ECJ_BOOTCLASSPATH) (run: make bootstrap)"; exit 2; fi
	@mkdir -p "$(ECJ_CLASSES_DIR)" "$(BUILD_DIR)"
	@find client/refactor -name '*.java' -print | sort > "$(ECJ_SOURCES_FILE)"
	@echo "Compiling fast with ECJ: $(ECJ_JAVA)"
	@"$(ECJ_JAVA)" -cp "$(ECJ_JAR)" org.eclipse.jdt.internal.compiler.batch.Main -nowarn -source 1.8 -target 1.8 -bootclasspath "$(ECJ_BOOTCLASSPATH)" -cp "$(LIBS)" -d "$(ECJ_CLASSES_DIR)" @"$(ECJ_SOURCES_FILE)"

rebuild-refactor-compile: rebuild-refactor compile-refactor

static-split-candidates:
	@$(REF_PY) -m tools.refactor.cli.static_split_candidates --src-dir client/refactor --scope-dir client/refactor/collections --out-md docs/static-split-candidates.md --out-csv docs/static-split-candidates.csv

extract-statics:
	@$(REF_PY) -m tools.refactor.cli.extract_statics_ts --src-dir client/refactor --manifest-dir client/refactor/.refactor-plan/extract-statics/generated --rename-csv "$(SYMBOLS_CSV)" --write-touched-files "$(EXTRACT_TOUCHED_FILES)" --max-manifests "$${MAX_MANIFESTS:--1}" --jobs "$${EXTRACT_JOBS:-1}" --rust-callsites "$${EXTRACT_RUST_CALLSITES:-build}"

extract-statics-dry:
	@$(REF_PY) -m tools.refactor.cli.extract_statics_ts --src-dir client/refactor --manifest-dir client/refactor/.refactor-plan/extract-statics/generated --rename-csv "$(SYMBOLS_CSV)" --write-touched-files "$(EXTRACT_TOUCHED_FILES)" --max-manifests "$${MAX_MANIFESTS:--1}" --jobs "$${EXTRACT_JOBS:-1}" --rust-callsites "$${EXTRACT_RUST_CALLSITES:-build}" --dry-run

check-static-extract:
	@$(REF_PY) -m tools.refactor.cli.check_static_extract --src-dir client/refactor --manifest-dir client/refactor/.refactor-plan/extract-statics/generated --rename-csv "$(SYMBOLS_CSV)"

extract-statics-loop:
	@$(MAKE) extract-statics
	@$(MAKE) check-static-extract
	@$(MAKE) compile-recursive SRC_DIR=client/refactor CLASSES_DIR=build/classes-refactor-layout SOURCES_FILE=build/sources-refactor-layout.txt

refactor-loop:
	@if [ "$${PROFILE:-0}" = "1" ]; then \
		mkdir -p "$(PROFILE_DIR)"; \
		printf 'stage\treal_s\tuser_s\tsys_s\tmaxrss_kb\n' > "$(PROFILE_FILE)"; \
	fi
	$(call RUN_WITH_PROFILE,refactor_pipeline,$(REF_PY) -m tools.refactor.orchestration.refactor_pipeline --max-renames "$${MAX_RENAMES:-20}" --max-manifests "$${MAX_MANIFESTS:-10}" --extract-rust-callsites "$${EXTRACT_RUST_CALLSITES:-build}" --rename-rust-prefilter "$${RENAME_RUST_PREFILTER:-off}" --resume --skip-class-renames --incremental-rename --report build/refactor-pipeline-report.md $${ALLOW_CONFLICTS:+--allow-conflicts} $${FAST_VALIDATE:+--fast-validate} $${LOOP_FAST_COMPILE:+--fast-compile})
	@if [ "$${PROFILE:-0}" = "1" ]; then \
		echo "Profile written: $(PROFILE_FILE)"; \
	fi

refactor-loop-dry:
	@$(REF_PY) -m tools.refactor.orchestration.refactor_pipeline --max-renames "$${MAX_RENAMES:-20}" --max-manifests "$${MAX_MANIFESTS:-10}" --extract-rust-callsites "$${EXTRACT_RUST_CALLSITES:-build}" --rename-rust-prefilter "$${RENAME_RUST_PREFILTER:-off}" --dry-run --resume --skip-class-renames --incremental-rename --report build/refactor-pipeline-report-dry.md $${ALLOW_CONFLICTS:+--allow-conflicts} $${LOOP_FAST_COMPILE:+--fast-compile}

build-refactor-views:
	@$(REF_PY) -m tools.refactor.cli.build_refactor_views --plan-dir client/refactor/.refactor-plan --refactor-src client/refactor --out-symbol-root client/refactor/.refactor-plan/generated/symbol_renames.csv --out-symbol-dir client/refactor/.refactor-plan/symbol-renames/generated --out-class-csv client/refactor/.refactor-plan/generated/classes.csv --out-extract-dir client/refactor/.refactor-plan/extract-statics/generated --report docs/refactor-views-report.md $${ALLOW_CONFLICTS:+--allow-conflicts}

build-refactor-class-view:
	@$(REF_PY) -m tools.refactor.cli.build_refactor_views --mode class-only --plan-dir client/refactor/.refactor-plan --out-class-csv client/refactor/.refactor-plan/generated/classes.csv --report docs/refactor-class-view-report.md

migrate-refactor-plan:
	@$(REF_PY) -m tools.refactor.planning.migrate_refactor_plan --plan-dir client/refactor/.refactor-plan --symbols-root-csv client/refactor/.refactor-plan/import/symbol_renames.csv --symbols-dir client/refactor/.refactor-plan/import/symbol-renames --extract-dir client/refactor/.refactor-plan/import/extract-statics --clean

test-refactor-tools-fast:
	@$(REF_PY) -m unittest -v \
		tools.refactor.tests.test_apply_cleanup_candidates \
		tools.refactor.tests.test_remap_cleanup_owners \
		tools.refactor.tests.test_signature_rewrite_semantic

test-refactor-tools-full:
	@$(REF_PY) -m unittest discover -s tools/refactor/tests -p "test_*.py" -v

test-refactor-tools: test-refactor-tools-full

verify-refactor-tooling:
	@$(MAKE) test-refactor-tools-fast
	@$(MAKE) build-refactor-views
	@$(MAKE) preflight-cleanup-refactor
	@$(MAKE) refactor-loop-dry
clean:
	rm -rf "$(BUILD_DIR)"

clean-refactor-cache:
	rm -rf "$(BUILD_DIR)/refactor-cache"

clean-refactor-tree:
	find client/refactor -type f -name '*.java' -delete
	find client/refactor -type d -empty -delete
	rm -rf "$(REFACTOR_STAMP_DIR)"

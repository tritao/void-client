SHELL := bash

SRC_DIR ?= client/src
BUILD_DIR ?= build
CLASSES_DIR ?= $(BUILD_DIR)/classes
SOURCES_FILE ?= $(BUILD_DIR)/sources.txt

LIBS ?= libs/clientlibs.jar
MAIN_CLASS ?= Loader
OUT_JAR ?= $(BUILD_DIR)/void-client.jar
CLASSES_STAMP ?= $(CLASSES_DIR)/.compiled.stamp
JAVA_ARGS ?=
EXCLUDE_REGEX ?=

# Requested JDK major version. Used to validate JAVA_HOME and auto-select a bootstrapped JDK under ./.jdk/.
JDK ?= 8
BOOTSTRAP_JAVA_HOME ?= $(CURDIR)/.jdk/temurin$(JDK)

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

.PHONY: help bootstrap sources compile jar run clean reports rename rename-dry rename-loop

help:
	@echo "Targets:"
	@echo "  make bootstrap - download repo-local JDK (./.jdk/temurin\$$JDK)"
	@echo "  make sources   - write $(SOURCES_FILE)"
	@echo "  make compile   - compile $(SRC_DIR) into $(CLASSES_DIR)"
	@echo "  make jar       - build runnable jar at $(OUT_JAR) (Main-Class: $(MAIN_CLASS))"
	@echo "  make run       - run $(MAIN_CLASS) using $(OUT_JAR) + $(LIBS)"
	@echo "  make reports   - regenerate docs/*.md reports"
	@echo "  make rename    - apply mappings from classes.csv (writes docs/rename-report.md)"
	@echo "  make rename-dry - dry-run mappings from classes.csv"
	@echo "  make rename-loop - rename + compile + reports"
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

$(CLASSES_STAMP): $(JAVA_SOURCES) $(LIB_JARS) | $(CLASSES_DIR) $(BUILD_DIR)
	@echo "Compiling with: $(JAVAC)"
	@find "$(SRC_DIR)" -maxdepth 1 -name '*.java' -print | sort > "$(SOURCES_FILE)"
	@if [ -n "$(EXCLUDE_REGEX)" ]; then grep -Ev "$(EXCLUDE_REGEX)" "$(SOURCES_FILE)" > "$(SOURCES_FILE).tmp" || true; mv "$(SOURCES_FILE).tmp" "$(SOURCES_FILE)"; fi
	@"$(JAVAC)" -Xlint:none -cp "$(LIBS)" -d "$(CLASSES_DIR)" @"$(SOURCES_FILE)"
	@touch "$(CLASSES_STAMP)"

compile: $(CLASSES_STAMP)

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

rename:
	@python tools/apply_class_renames.py --csv classes.csv --src-dir client/src --report docs/rename-report.md --max-renames "$${MAX_RENAMES:-20}"

rename-dry:
	@python tools/apply_class_renames.py --csv classes.csv --src-dir client/src --report docs/rename-report.md --max-renames "$${MAX_RENAMES:-20}" --dry-run

rename-loop:
	@$(MAKE) rename
	@$(MAKE) compile
	@$(MAKE) reports
	@echo "Done. See:"
	@echo "  docs/rename-report.md"
	@echo "  docs/unnamed-status.md"
	@echo "  docs/fan-graph.md"
	@echo "  docs/rename-dossiers.md"

clean:
	rm -rf "$(BUILD_DIR)"

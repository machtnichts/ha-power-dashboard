# deyepv (Rust) - build and verification
#
# `make`       release build
# `make static`  fully static build, no libc dependency
# `make check`   every test and verification, in one shot
# `make install` copy the binary to bin/ (what the systemd unit expects)

CARGO          ?= cargo
TARGET_TRIPLE  ?= x86_64-unknown-linux-musl
BIN            := target/release/deyepv
STATIC_BIN     := target/$(TARGET_TRIPLE)/release/deyepv

.PHONY: all build static test unit check cross differential diffcsv stubcheck install clean help

all: build

build:
	$(CARGO) build --release --offline
	@ls -l $(BIN)

static:
	$(CARGO) build --release --offline --target $(TARGET_TRIPLE)
	@file $(STATIC_BIN)

test unit:
	$(CARGO) test --offline

# the original Python poller, patched copy pointed at a stub logger
cross stubcheck:
	python3 tools/check_python_against_stub.py

# Python poller vs Rust poller: every published message, byte for byte
differential:
	python3 tools/differential_test.py

# the comparison CSV row, field by field
diffcsv:
	python3 tools/differential_csv.py

check: test differential diffcsv
	@echo
	@echo "=== all checks passed ==="

install:
	@if [ -f $(STATIC_BIN) ]; then \
		$(CARGO) build --release --offline --target $(TARGET_TRIPLE) && \
		mkdir -p bin && cp $(STATIC_BIN) bin/deyepv && echo "installed bin/deyepv (static, no libc dependency)"; \
	else \
		$(CARGO) build --release --offline && mkdir -p bin && cp $(BIN) bin/deyepv && \
		echo "installed bin/deyepv (glibc; run 'make static' for a fully static one)"; \
	fi

clean:
	$(CARGO) clean

help:
	@sed -n '2,9p' Makefile

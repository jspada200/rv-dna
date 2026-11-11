VERSION := $(shell sed -n 's/^version: *//p' PACKAGE)
PKG := dist/dna-$(VERSION).rvpkg
SOURCES := PACKAGE rv_dna_mode.py rv_dna_auth.py rv_dna_signin_dialog.py \
	rv_dna_version_id.py rv_dna_in_review.py version_id_paths.json

RV_APP ?= /Applications/RV.app
RVPKG ?= $(RV_APP)/Contents/MacOS/rvpkg
PYTHON ?= $(RV_APP)/Contents/MacOS/python3
RV_SUPPORT_AREA ?= $(HOME)/Library/Application Support/RV

.PHONY: package install uninstall test clean

package: $(PKG)

$(PKG): $(SOURCES)
	@mkdir -p dist
	rm -f $@
	zip -j $@ $(SOURCES)

install: package
	-"$(RVPKG)" -force -uninstall "$(RV_SUPPORT_AREA)/Packages/$(notdir $(PKG))"
	-"$(RVPKG)" -force -remove "$(RV_SUPPORT_AREA)/Packages/$(notdir $(PKG))"
	"$(RVPKG)" -force -install -add "$(RV_SUPPORT_AREA)" $(PKG)

uninstall:
	"$(RVPKG)" -force -uninstall "$(RV_SUPPORT_AREA)/Packages/$(notdir $(PKG))"
	"$(RVPKG)" -force -remove "$(RV_SUPPORT_AREA)/Packages/$(notdir $(PKG))"

test:
	"$(PYTHON)" -m unittest discover -s tests -v

clean:
	rm -rf dist

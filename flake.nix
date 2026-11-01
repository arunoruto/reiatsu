{
  description = "Reiatsu: wall-aware BLE room locator";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAll (pkgs: rec {
        reiatsu = pkgs.python3Packages.buildPythonPackage {
          pname = "reiatsu";
          version = "0.4.2";
          pyproject = true;
          src = ./.;
          build-system = [ pkgs.python3Packages.setuptools ];
          dependencies = with pkgs.python3Packages; [
            numpy
            scipy
            pyyaml
            paho-mqtt
          ];
          nativeCheckInputs = [ pkgs.python3Packages.pytestCheckHook ];
        };
        default = reiatsu;
      });

      devShells = forAll (pkgs: {
        # bridge tests against nixpkgs Home Assistant: nix develop .#ha -c pytest tests_ha
        ha = pkgs.mkShell {
          packages = [
            (pkgs.home-assistant.python3Packages.python.withPackages (
              p:
              [
                p.pytest
                p.pytest-homeassistant-custom-component
              ]
              ++ pkgs.home-assistant.getPackages "bluetooth" p
              ++ pkgs.home-assistant.getPackages "mqtt" p
            ))
          ];
        };
        # docs site: nix develop .#docs -c sphinx-build -W docs docs/_build
        docs = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (
              p:
              let
                # not in nixpkgs: shibuya theme and its pygments-styles dependency, from PyPI wheels
                wheel =
                  pname: version: hash: deps:
                  p.buildPythonPackage {
                    inherit pname version;
                    format = "wheel";
                    src = pkgs.fetchPypi {
                      inherit pname version hash;
                      format = "wheel";
                      dist = "py3";
                      python = "py3";
                    };
                    dependencies = deps;
                  };
                pygments-styles = wheel "pygments_styles" "0.3.0" "sha256-xsRemTnrdZA0W8kIQRO6xGxF8SsAnRNCK+AugOhKA0w=" [
                  p.pygments
                ];
                shibuya = wheel "shibuya" "2026.7.12" "sha256-CiwMEJ58xH/GULAf62cdJnwP/i45yY81KeMtcjqcnZs=" [
                  p.sphinx
                  pygments-styles
                ];
              in
              with p;
              [
                numpy
                scipy
                pyyaml
                paho-mqtt
                sphinx
                shibuya
                myst-parser
                sphinx-copybutton
                sphinxcontrib-mermaid
              ]
            ))
          ];
        };
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (
              p: with p; [
                numpy
                scipy
                pyyaml
                paho-mqtt
                pytest
              ]
            ))
          ];
        };
      });
    };
}

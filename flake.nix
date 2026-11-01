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

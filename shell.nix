{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  packages = with pkgs; [
    (python3.withPackages (ps: with ps; [
      numpy
      scipy
      soundfile
      weasyprint
    ]))
    wavpack
    monkeys-audio
    ffmpeg
    sqlite
    texliveFull
    xml2rfc
    mmark
    python3Packages.weasyprint
    pango
    noto-fonts
    roboto-mono
  ];
}

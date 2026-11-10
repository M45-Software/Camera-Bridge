# Third-party runtime

The amd64 Camera Bridge package includes the unmodified Canon EOS Digital SDK
runtime `libEDSDK.so`. This is proprietary software owned by Canon Inc. It is
provided with Camera Bridge for communication with compatible Canon cameras;
it is not covered by the license of Camera Bridge's own source code.

Canon's developer FAQ permits distributing EDSDK libraries and program headers
with an application, and prohibits distributing the other SDK contents or the
SDK package itself:
<https://developers.canon-europe.com/developers/s/article/camera-faq>

Only the runtime library is included. SDK documentation, samples, headers and the
complete SDK package are not included. Canon's applicable SDK agreement governs
the runtime; this notice does not grant additional rights to Canon software.
Camera Bridge is independent of Canon and is not endorsed or supported by Canon.

## Runtime provenance

- Source: Tahoma2D 1.6.3, `Tahoma2D-linux-clang.deb`, official project release:
  <https://github.com/tahoma2d/tahoma2d/releases/tag/v1.6.3>
- Package member: `etc/skel/.local/share/Tahoma2D/usr/lib/libEDSDK.so`
- Architecture: Linux x86-64.
- SHA-256: `92aeae98d2d18f641df72695733d68a55993b4149a13a075983895775eaf91b5`
- SDK version has not been independently established; the checksum identifies
  the tested runtime. Retain its embedded copyright notices unchanged.

System libraries, including libusb, are installed through APT and retain their
respective licenses.

# EXIF Metadata

reFrame writes standard EXIF metadata to captured originals and processed images. The original capture remains the source of truth when a photo is reprocessed.

## Capture fields

- `Make`: configured camera make, default `Raspberry Pi`.
- `Model`: configured camera model, default `ReFrame Camera`.
- `LensMake` and `LensModel`: detected or configured hardware, default `Raspberry Pi Camera Module 3`.
- `Software`: `reFrame` plus the operating-system identity reported by the host.
- `Artist`, `Copyright`, and `ImageDescription`: optional values from the `metadata` settings section.
- `DateTime`, `DateTimeOriginal`, and `DateTimeDigitized`, with offset and subsecond fields where available.
- Exposure time, ISO, aperture, focal length, focus distance, white balance, light source, and pixel dimensions when the camera supplies the corresponding value.

Capture time is also stored as a scalar `capture_time` value in the reFrame metadata envelope. Sensor metadata remains grouped under `sensor`; it is not used as the gallery capture-date value.

## Orientation

The camera has no rotation sensor, so new captures use EXIF orientation `1`. Gallery rotation updates only the EXIF orientation tag and leaves image pixels unchanged. The gallery reads that tag for both preview rendering and saved-image rotation state.

## Reprocessing

Reprocessing copies standard EXIF, GPS, and interoperability IFD values from the original image, then updates processing metadata and the file modification date. Existing camera identity, author fields, source software, capture time, and sensor metadata are reused when the source contains them.

The processed PNG also carries reFrame sidecar text fields for dithering settings and the structured capture metadata envelope. These fields allow the dashboard to display the same information even when a reader does not expose all nested EXIF values.
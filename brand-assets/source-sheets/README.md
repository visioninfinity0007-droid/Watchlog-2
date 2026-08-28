# Identity sheets — drop the original images here

Save the five identity sheets exactly as they came out of the generator.
Do not crop, resize, or re-export them; the extractor reads the original
pixels and any re-encoding loses edge quality.

Suggested names (any name works, these just make the log readable):

    01-logo-construction.png
    02-lockups.png
    03-colour-system.png
    04-type-specimen.png
    05-icon-set.png

PNG is much better than JPG here. JPG compression puts coloured fringing
around the hard edges of the mark, which the colour matcher then picks up
as part of the shape. If you only have JPG it will still work, just with
a slightly rougher outline.

Then run:

    python tools/extract_assets.py --list

which reports every violet and near-black shape it can find in each
sheet, with sizes and positions. Nothing is written until you extract.

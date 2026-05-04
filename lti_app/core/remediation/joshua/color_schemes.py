"""LACCD campus color schemes for the Joshua Design System."""

from lti_app.models import Campus, ColorScheme


class ColorSchemes:
    """Campus color schemes for LACCD institutions."""

    # Official LACCD campus colors
    SCHEMES: dict[Campus, ColorScheme] = {
        Campus.LACCD: ColorScheme(
            primary="#003D66",
            secondary="#005a97",
        ),
        Campus.ELAC: ColorScheme(
            primary="#01573D",
            secondary="#FDB040",
        ),
        Campus.LACC: ColorScheme(
            primary="#C13D40",
            secondary="#305589",
        ),
        Campus.LAHC: ColorScheme(
            primary="#FCAC4F",
            secondary="#001A72",
        ),
        Campus.LAMC: ColorScheme(
            primary="#00205C",
            secondary="#979797",
        ),
        Campus.LAPC: ColorScheme(
            primary="#090909",
            secondary="#EE3125",
        ),
        Campus.LASC: ColorScheme(
            primary="#002856",
            secondary="#FCC60E",
        ),
        Campus.LATTC: ColorScheme(
            primary="#572E82",
            secondary="#FBB517",
        ),
        Campus.LAVC: ColorScheme(
            primary="#20680C",
            secondary="#FCB926",
        ),
        Campus.WLAC: ColorScheme(
            primary="#003882",
            secondary="#FCC917",
        ),
    }

    # Campus display names
    CAMPUS_NAMES: dict[Campus, str] = {
        Campus.LACCD: "Los Angeles Community College District",
        Campus.ELAC: "East Los Angeles College",
        Campus.LACC: "Los Angeles City College",
        Campus.LAHC: "Los Angeles Harbor College",
        Campus.LAMC: "Los Angeles Mission College",
        Campus.LAPC: "Los Angeles Pierce College",
        Campus.LASC: "Los Angeles Southwest College",
        Campus.LATTC: "Los Angeles Trade Tech College",
        Campus.LAVC: "Los Angeles Valley College",
        Campus.WLAC: "West Los Angeles College",
    }

    @classmethod
    def get_scheme(cls, campus: Campus) -> ColorScheme:
        """Get the color scheme for a campus.

        Args:
            campus: Campus identifier.

        Returns:
            ColorScheme with primary and secondary colors.
        """
        return cls.SCHEMES.get(campus, cls.SCHEMES[Campus.LACCD])

    @classmethod
    def get_campus_name(cls, campus: Campus) -> str:
        """Get the display name for a campus.

        Args:
            campus: Campus identifier.

        Returns:
            Full campus name.
        """
        return cls.CAMPUS_NAMES.get(campus, "LACCD")

    @classmethod
    def list_campuses(cls) -> list[dict]:
        """List all available campuses with their colors.

        Returns:
            List of dictionaries with campus info.
        """
        return [
            {
                "id": campus.value,
                "name": cls.CAMPUS_NAMES[campus],
                "colors": cls.SCHEMES[campus].model_dump(),
            }
            for campus in Campus
            if campus != Campus.CUSTOM
        ]

    @classmethod
    def validate_contrast(cls, scheme: ColorScheme) -> dict:
        """Validate that a color scheme meets WCAG contrast requirements.

        Args:
            scheme: ColorScheme to validate.

        Returns:
            Dictionary with validation results.
        """
        from lti_app.core.accessibility.rules.contrast import (
            hex_to_rgb,
            contrast_ratio,
        )

        # Check primary color against white (for text on primary background)
        primary_rgb = hex_to_rgb(scheme.primary)
        white_rgb = (255, 255, 255)

        if primary_rgb:
            primary_white_ratio = contrast_ratio(primary_rgb, white_rgb)
        else:
            primary_white_ratio = 0

        # Check secondary color against primary
        secondary_rgb = hex_to_rgb(scheme.secondary)

        if primary_rgb and secondary_rgb:
            primary_secondary_ratio = contrast_ratio(primary_rgb, secondary_rgb)
        else:
            primary_secondary_ratio = 0

        return {
            "primary_on_white": {
                "ratio": round(primary_white_ratio, 2),
                "passes_aa": primary_white_ratio >= 4.5,
                "passes_aaa": primary_white_ratio >= 7.0,
            },
            "secondary_on_primary": {
                "ratio": round(primary_secondary_ratio, 2),
                "passes_aa": primary_secondary_ratio >= 4.5,
                "passes_aaa": primary_secondary_ratio >= 7.0,
            },
        }

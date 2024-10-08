import os
from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsVectorLayer,
    QgsField,
    QgsSpatialIndex,
    QgsProcessingFeedback,
)
import processing
from .create_grids import GridCreator
from .extents import Extents


class RasterPolylineGridScore:
    def __init__(self, country_boundary, pixel_size, output_path, crs, input_polylines):
        self.country_boundary = country_boundary
        self.pixel_size = pixel_size
        self.output_path = output_path
        self.crs = crs
        self.input_polylines = input_polylines

    def raster_polyline_grid_score(self):
        """
        Generates a raster based on the number of input points within each grid cell.
        :param country_boundary: Layer defining the country boundary to clip the grid.
        :param cellsize: The size of each grid cell.
        :param output_path: Path to save the output raster.
        :param crs: The CRS in which the grid and raster will be projected.
        :param input_polylines: Layer of point features to count within each grid cell.
        """

        # Create grid
        self.h_spacing = 100
        self.v_spacing = 100
        create_grid = GridCreator(h_spacing=self.h_spacing, v_spacing=self.v_spacing)
        output_dir = os.path.join("output")
        merged_output_path = os.path.join(output_dir, "merged_grid.shp")
        grid_layer = create_grid.create_grids(
            self.country_boundary, output_dir, self.crs, merged_output_path
        )
        grid_layer = QgsVectorLayer(merged_output_path, "merged_grid", "ogr")

        # Add score field
        provider = grid_layer.dataProvider()
        field_name = "line_score"
        if not grid_layer.fields().indexFromName(field_name) >= 0:
            provider.addAttributes([QgsField(field_name, QVariant.Int)])
            grid_layer.updateFields()

        # Create spatial index for the input points
        # Reproject the country layer if necessary
        if self.input_polylines.crs() != self.crs:
            self.input_polylines = processing.run(
                "native:reprojectlayer",
                {
                    "INPUT": self.input_polylines,
                    "TARGET_CRS": self.crs,
                    "OUTPUT": "memory:",
                },
                feedback=QgsProcessingFeedback(),
            )["OUTPUT"]
        polyline_index = QgsSpatialIndex(self.input_polylines.getFeatures())

        # Count points within each grid cell and assign a score
        reclass_vals = {}
        for grid_feat in grid_layer.getFeatures():
            grid_geom = grid_feat.geometry()
            # Get intersecting points
            intersecting_ids = polyline_index.intersects(grid_geom.boundingBox())

            # Initialize a set to store unique intersecting line feature IDs
            unique_intersections = set()

            # Check each potentially intersecting line feature
            for line_id in intersecting_ids:
                line_feat = self.input_polylines.getFeature(line_id)
                line_geom = line_feat.geometry()

                # Perform a detailed intersection check
                if grid_feat.geometry().intersects(line_geom):
                    unique_intersections.add(line_id)

            num_polylines = len(unique_intersections)

            # Reclassification logic: assign score based on the number of points
            if num_polylines >= 2:
                reclass_val = 5
            elif num_polylines == 1:
                reclass_val = 3
            else:
                reclass_val = 0

            reclass_vals[grid_feat.id()] = reclass_val

        # Step 5: Apply the score values to the grid
        grid_layer.startEditing()
        for grid_feat in grid_layer.getFeatures():
            grid_layer.changeAttributeValue(
                grid_feat.id(),
                provider.fieldNameIndex(field_name),
                reclass_vals[grid_feat.id()],
            )
        grid_layer.commitChanges()

        merged_output_vector = os.path.join(output_dir, "merged_grid_vector.shp")

        Merge = processing.run(
            "native:mergevectorlayers",
            {"LAYERS": [grid_layer], "CRS": None, "OUTPUT": "memory:"},
            feedback=QgsProcessingFeedback(),
        )

        merge = Merge["OUTPUT"]

        extents_processor = Extents(
            output_dir, self.country_boundary, self.pixel_size, self.crs
        )

        # Get the extent of the vector layer
        country_extent = extents_processor.get_country_extent()
        xmin, ymin, xmax, ymax = (
            country_extent.xMinimum(),
            country_extent.yMinimum(),
            country_extent.xMaximum(),
            country_extent.yMaximum(),
        )

        # Rasterize the clipped grid layer to generate the raster
        rasterize_params = {
            "INPUT": merge,
            "FIELD": field_name,
            "BURN": 0,
            "USE_Z": False,
            "UNITS": 1,
            "WIDTH": self.pixel_size,
            "HEIGHT": self.pixel_size,
            "EXTENT": f"{xmin},{xmax},{ymin},{ymax}",
            "NODATA": -9999,
            "OPTIONS": "",
            "DATA_TYPE": 5,  # Use Int32 for scores
            "OUTPUT": self.output_path,
        }

        processing.run(
            "gdal:rasterize", rasterize_params, feedback=QgsProcessingFeedback()
        )

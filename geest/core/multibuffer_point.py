from qgis.core import (
    QgsVectorLayer,
    QgsField,
    QgsFeatureRequest,
    QgsProcessingFeedback,
    QgsCoordinateReferenceSystem,
    QgsProcessingException,
    edit,
)
from PyQt5.QtCore import QVariant
import processing


class MultiBufferCreator:
    def __init__(self, distance_list, subset_size=5):
        """
        Initialize the MultiBufferCreator class.

        :param distance_list: List of buffer distances (in meters or seconds if using time-based buffers)
        :param subset_size: Number of features to process in each subset
        """
        self.distance_list = distance_list
        self.subset_size = subset_size

    # TODO: refactor function
    def create_multibuffers(
        self,
        point_layer,
        output_path,
        mode="driving-car",
        measurement="distance",
        crs="EPSG:4326",
    ):
        """
        Creates multibuffers for each point in the input point layer.

        :param point_layer: QgsVectorLayer containing point features
        :param output_path: Path to save the merged output layer
        :param mode: Mode of travel for ORS API (e.g., 'driving-car')
        :param measurement: 'distance' or 'time'
        :param crs: Coordinate reference system (default is WGS84)
        :return: QgsVectorLayer containing the buffers as polygons
        """
        # Prepare to collect intermediate layers
        temp_layers = []
        features = list(point_layer.getFeatures())
        total_features = len(features)

        # Process features in subsets to handle large datasets
        for i in range(0, total_features, self.subset_size):
            subset_features = features[i : i + self.subset_size]
            # Create a temporary layer for the subset
            subset_layer = QgsVectorLayer(f"Point?crs={crs}", f"subset_{i}", "memory")
            subset_layer_data = subset_layer.dataProvider()
            subset_layer_data.addAttributes(point_layer.fields())
            subset_layer.updateFields()
            subset_layer_data.addFeatures(subset_features)

            # Prepare parameters for ORS isochrones tool

            params = {
                "INPUT_PROVIDER": 0,  # 0 for OpenRouteService
                "INPUT_PROFILE": mode,
                "INPUT_POINT_LAYER": subset_layer,
                "INPUT_FIELD": "",  # No specific field for ranges
                "INPUT_METRIC": 0 if measurement == "distance" else 1,
                "INPUT_UNITS": 0,  # 0 for meters/seconds
                "INPUT_RANGES": ",".join(map(str, self.distance_list)),
                "INPUT_DATE_TIME": None,
                "INPUT_INTERVAL": None,
                "INPUT_SMOOTHING": None,
                "INPUT_SRID": "",
                "LOCATION_TYPE": 0,
                "INPUT_AVOID_FEATURES": "",
                "INPUT_AVOID_BORDERS": "",
                "INPUT_AVOID_COUNTRIES": "",
                "INPUT_AVOID_POLYGONS": "",
                "OUTPUT": "memory:",
            }

            try:
                # Run the ORS isochrones tool
                result = processing.run(
                    "ORS Tools:isochrones_from_layer",
                    params,
                    feedback=QgsProcessingFeedback(),
                )
                isochrone_layer = result["OUTPUT"]
                temp_layers.append(isochrone_layer)

                # Provide progress feedback
                print(
                    f"Processed subset {i + 1} to {min(i + self.subset_size, total_features)} of {total_features}"
                )

            except QgsProcessingException as e:
                print(
                    f"Error processing subset {i + 1} to {min(i + self.subset_size, total_features)}: {e}"
                )
                continue

        # Merge all isochrone layers into one
        if temp_layers:
            merge_params = {
                "LAYERS": temp_layers,
                "CRS": QgsCoordinateReferenceSystem(crs),
                "OUTPUT": "memory:",
            }
            merged_result = processing.run("native:mergevectorlayers", merge_params)
            merged_layer = merged_result["OUTPUT"]

            # Proceed to create bands by differencing the ranges
            self._create_bands(merged_layer, output_path, crs)
        else:
            print("No isochrones were created.")

    def _create_bands(self, merged_layer, output_path, crs):
        """
        Creates bands by differencing isochrone ranges.

        :param merged_layer: The merged isochrone layer
        :param output_path: Path to save the final output layer
        :param crs: Coordinate reference system
        """
        # Extract unique ranges from the 'value' field added by ORS
        ranges_field = "value"
        unique_ranges = sorted(
            {feat[ranges_field] for feat in merged_layer.getFeatures()}
        )

        # Create dissolved layers for each range
        range_layers = {}
        for r in unique_ranges:
            # Select features matching the current range
            expr = f'"{ranges_field}" = {r}'
            request = QgsFeatureRequest().setFilterExpression(expr)
            features = [feat for feat in merged_layer.getFeatures(request)]
            if features:
                # Create a memory layer for this range
                range_layer = QgsVectorLayer(
                    f"Polygon?crs={crs}", f"range_{r}", "memory"
                )
                dp = range_layer.dataProvider()
                dp.addAttributes(merged_layer.fields())
                range_layer.updateFields()
                dp.addFeatures(features)

                # Dissolve the range layer to create a single feature
                dissolve_params = {
                    "INPUT": range_layer,
                    "FIELD": [],
                    "OUTPUT": "memory:",
                }
                dissolve_result = processing.run("native:dissolve", dissolve_params)
                dissolved_layer = dissolve_result["OUTPUT"]
                range_layers[r] = dissolved_layer

        # Create bands by computing differences between the ranges
        band_layers = []
        sorted_ranges = sorted(range_layers.keys(), reverse=True)
        for i in range(len(sorted_ranges) - 1):
            current_range = sorted_ranges[i]
            next_range = sorted_ranges[i + 1]
            current_layer = range_layers[current_range]
            next_layer = range_layers[next_range]

            # Difference between current and next range
            difference_params = {
                "INPUT": current_layer,
                "OVERLAY": next_layer,
                "OUTPUT": "memory:",
            }
            diff_result = processing.run("native:difference", difference_params)
            diff_layer = diff_result["OUTPUT"]

            # Add 'rasField' attribute to store the range value
            diff_layer.dataProvider().addAttributes(
                [QgsField("rasField", QVariant.Int)]
            )
            diff_layer.updateFields()
            with edit(diff_layer):
                for feat in diff_layer.getFeatures():
                    feat["rasField"] = current_range
                    diff_layer.updateFeature(feat)

            band_layers.append(diff_layer)

        # Handle the smallest range separately
        smallest_range = sorted_ranges[-1]
        smallest_layer = range_layers[smallest_range]
        smallest_layer.dataProvider().addAttributes(
            [QgsField("rasField", QVariant.Int)]
        )
        smallest_layer.updateFields()
        with edit(smallest_layer):
            for feat in smallest_layer.getFeatures():
                feat["rasField"] = smallest_range
                smallest_layer.updateFeature(feat)
        band_layers.append(smallest_layer)

        # Merge all band layers into the final output
        merge_bands_params = {
            "LAYERS": band_layers,
            "CRS": QgsCoordinateReferenceSystem(crs),
            "OUTPUT": output_path,
        }
        final_merge_result = processing.run(
            "native:mergevectorlayers", merge_bands_params
        )
        final_layer = QgsVectorLayer(output_path, "MultiBuffer", "ogr")

        print(f"Multi-buffer layer created at {output_path}")

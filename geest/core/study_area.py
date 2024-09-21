# core/study_area.py
import os
import re
from qgis.core import (
    QgsRectangle,
    QgsFeature,
    QgsGeometry,
    QgsField,
    QgsProject,
    QgsCoordinateTransform,
    QgsCoordinateReferenceSystem,
    QgsWkbTypes,
    QgsVectorLayer,
    QgsVectorFileWriter,
    QgsFields,
    QgsCoordinateTransformContext,
)
from qgis.PyQt.QtCore import QVariant

class StudyAreaProcessor:
    def __init__(self, layer, working_dir):
        self.layer = layer
        self.working_dir = working_dir
        self.gpkg_path = os.path.join(self.working_dir, "study_area.gpkg")

    def create_bbox_multiple_100m(self, bbox):
        """Adjusts bounding box dimensions to be a multiple of 100m."""
        crs_src = QgsCoordinateReferenceSystem(self.layer.crs())  # Source CRS
        crs_dst = QgsCoordinateReferenceSystem("EPSG:3857")  # EPSG:3857 (meters)
        transform = QgsCoordinateTransform(crs_src, crs_dst, QgsProject.instance())

        # Transform the bounding box
        x_min, y_min = transform.transform(bbox.xMinimum(), bbox.yMinimum())
        x_max, y_max = transform.transform(bbox.xMaximum(), bbox.yMaximum())

        # Adjust bbox dimensions to be exact multiples of 100m
        def make_multiple(val, mult):
            return mult * round(val / mult)

        x_min = make_multiple(x_min, 100)
        y_min = make_multiple(y_min, 100)
        x_max = make_multiple(x_max, 100)
        y_max = make_multiple(y_max, 100)

        return QgsRectangle(x_min, y_min, x_max, y_max)

    def create_layer_if_not_exists(self, layer_name, fields, geometry_type):
        """Create a new layer in the GeoPackage if it doesn't already exist."""
        gpkg_layer_path = f"{self.gpkg_path}|layername={layer_name}"
        layer = QgsVectorLayer(gpkg_layer_path, layer_name, "ogr")

        if not layer.isValid():  # Layer does not exist, create it
            crs = QgsCoordinateReferenceSystem("EPSG:3857")
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.fileEncoding = "UTF-8"

            # Convert list of QgsField objects to QgsFields object
            qgs_fields = QgsFields()
            for field in fields:
                qgs_fields.append(field)

            # Create a new GeoPackage layer
            QgsVectorFileWriter.create(
                fileName=self.gpkg_path,
                fields=qgs_fields,
                geometryType=geometry_type,
                srs=crs,
                transformContext=QgsCoordinateTransformContext(),
                options=options
            )

    def append_to_layer(self, layer_name, features):
        """Append features to an existing layer in the GeoPackage."""
        gpkg_layer_path = f"{self.gpkg_path}|layername={layer_name}"
        gpkg_layer = QgsVectorLayer(gpkg_layer_path, layer_name, "ogr")

        if gpkg_layer.isValid():
            provider = gpkg_layer.dataProvider()
            provider.addFeatures(features)
            gpkg_layer.updateExtents()

    def save_to_geopackage(self, features, layer_name, fields, geometry_type):
        """Save features to GeoPackage."""
        self.create_layer_if_not_exists(layer_name, fields, geometry_type)
        self.append_to_layer(layer_name, features)

    def create_and_save_grid(self, bbox, study_area_geom):
        """Creates a 100m grid over the bounding box and saves it directly to the GeoPackage table."""
        grid_features = []
        grid_layer_name = "grids"
        grid_fields = [QgsField("id", QVariant.Int)]

        x_min = bbox.xMinimum()
        x_max = bbox.xMaximum()
        y_min = bbox.yMinimum()
        y_max = bbox.yMaximum()
        step = 100
        feature_id = 0

        for x in range(int(x_min), int(x_max), step):
            for y in range(int(y_min), int(y_max), step):
                rect = QgsRectangle(x, y, x + step, y + step)
                grid_geom = QgsGeometry.fromRect(rect)

                # Only keep the grid cells that intersect with the study area geometry
                if grid_geom.intersects(study_area_geom):
                    feature = QgsFeature()
                    feature.setGeometry(grid_geom)
                    feature.setAttributes([feature_id])
                    grid_features.append(feature)
                    feature_id += 1

        # Save grid cells directly to the GeoPackage
        self.save_to_geopackage(grid_features, grid_layer_name, grid_fields, QgsWkbTypes.Polygon)

    def process_multipart_geometry(self, geom, normalized_name, area_name, study_area_dir):
        """Processes each part of a multipart geometry feature."""
        parts = geom.asGeometryCollection()
        part_count = 1
        study_area_features = []
        study_area_fields = [QgsField("area_name", QVariant.String)]

        for part in parts:
            bbox = part.boundingBox()
            bbox_100m = self.create_bbox_multiple_100m(bbox)

            # Create study area feature
            feature = QgsFeature()
            feature.setGeometry(QgsGeometry.fromRect(bbox_100m))
            feature.setAttributes([area_name])
            study_area_features.append(feature)

            # Create and save grid for this part
            self.create_and_save_grid(bbox_100m, part)

            part_count += 1

        # Save study area features directly to the GeoPackage
        self.save_to_geopackage(study_area_features, "study_area", study_area_fields, QgsWkbTypes.Polygon)

    def process_singlepart_geometry(self, geom, normalized_name, area_name, study_area_dir):
        """Processes a singlepart geometry feature."""
        study_area_features = []
        study_area_fields = [QgsField("area_name", QVariant.String)]

        bbox = geom.boundingBox()
        bbox_100m = self.create_bbox_multiple_100m(bbox)

        # Create study area feature
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromRect(bbox_100m))
        feature.setAttributes([area_name])
        study_area_features.append(feature)

        # Create and save grid for this part
        self.create_and_save_grid(bbox_100m, geom)

        # Save study area features directly to the GeoPackage
        self.save_to_geopackage(study_area_features, "study_area", study_area_fields, QgsWkbTypes.Polygon)

    def process_study_area(self, field_name, study_area_dir):
        """Processes each feature in the layer to create bounding boxes and grids."""
        selected_features = self.layer.selectedFeatures()
        features = selected_features if selected_features else self.layer.getFeatures()

        for feature in features:
            geom = feature.geometry()
            area_name = feature[field_name]
            normalized_name = re.sub(r"\s+", "_", area_name.lower())

            if geom.isMultipart():
                self.process_multipart_geometry(geom, normalized_name, area_name, study_area_dir)
            else:
                self.process_singlepart_geometry(geom, normalized_name, area_name, study_area_dir)

    def add_layers_to_qgis(self):
        """Adds the generated layers from the GeoPackage to QGIS."""
        group = QgsProject.instance().layerTreeRoot().addGroup("study area")

        for layer_name in ["study_area", "grids"]:
            layer = QgsVectorLayer(f"{self.gpkg_path}|layername={layer_name}", layer_name, "ogr")
            if layer.isValid():
                QgsProject.instance().addMapLayer(layer, False)
                group.addLayer(layer)

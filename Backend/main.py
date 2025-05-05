from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from typing import List
import tempfile
import os
import trimesh
import numpy as np
from trimesh.transformations import rotation_matrix
from trimesh.geometry import align_vectors
from Backend.process_model import router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

async def upload_stl(file: UploadFile = File(...)):
    # Save uploaded file to a temporary location
    with tempfile.NamedTemporaryFile(delete=False, suffix=".stl") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

   

def calculate_dimensions(mesh: trimesh.Trimesh):
    bounds = mesh.bounds  # shape (2,3): min xyz, max xyz
    dims = bounds[1] - bounds[0]
    return {"x": float(dims[0]), "y": float(dims[1]), "z": float(dims[2])}



app.include_router(router)

@app.post("/auto-orient/")
async def auto_orient(files: List[UploadFile] = File(...)):
    results = []

    for file in files:
        # Save uploaded STL file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix=".stl") as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name

        # Load mesh
        mesh = trimesh.load(tmp_path, force='mesh')
        if not isinstance(mesh, trimesh.Trimesh):
            mesh = mesh.dump(concatenate=True)

        # --- Dimensions before any transformation ---
        dims_before = calculate_dimensions(mesh)

        # --- Step 1: PCA alignment ---
        centroid = mesh.centroid
        points_centered = mesh.vertices - centroid
        cov = np.cov(points_centered.T)
        eigenvalues, eigenvectors = np.linalg.eigh(cov)
        order = np.argsort(eigenvalues)[::-1]  # Descending order
        eigenvectors = eigenvectors[:, order]

        transform_pca = np.eye(4)
        transform_pca[:3, :3] = eigenvectors.T
        transform_pca[:3, 3] = -centroid @ eigenvectors
        mesh.apply_transform(transform_pca)

        # --- Step 2: Align longest dimension to X, second to Y, shortest to Z ---
        dims_after_pca = calculate_dimensions(mesh)
        dims_list = [dims_after_pca['x'], dims_after_pca['y'], dims_after_pca['z']]
        sorted_indices = np.argsort(dims_list)[::-1]  # Indices of dims sorted descending

        # We want: X = longest, Y = second, Z = shortest
        # Current order is x=0, y=1, z=2
        # If not in order, rotate accordingly

        # Simple heuristic: if longest dim not X, rotate 90 deg around Z
        if sorted_indices[0] != 0:
            if sorted_indices[0] == 1:  # longest is Y, rotate -90 deg around Z to swap X/Y
                mesh.apply_transform(rotation_matrix(np.radians(-90), [0, 0, 1]))
            elif sorted_indices[0] == 2:  # longest is Z, rotate 90 deg around Y to bring Z to X
                mesh.apply_transform(rotation_matrix(np.radians(90), [0, 1, 0]))

        # After this, re-check dims and reorder if needed for Y and Z
        dims_after_rotate = calculate_dimensions(mesh)
        dims_list = [dims_after_rotate['x'], dims_after_rotate['y'], dims_after_rotate['z']]
        sorted_indices = np.argsort(dims_list)[::-1]

        # If second longest is not Y, rotate 90 deg around X to swap Y/Z
        if sorted_indices[1] != 1:
            mesh.apply_transform(rotation_matrix(np.radians(90), [1, 0, 0]))

        # --- Step 3: Flat base alignment to Z-down ---
        z_down = np.array([0, 0, -1])
        face_normals = mesh.face_normals
        flat_faces = [
            i for i in range(len(face_normals))
            if np.degrees(np.arccos(np.clip(np.dot(face_normals[i], z_down), -1.0, 1.0))) < 10
        ]
        if flat_faces:
            largest_face = max(flat_faces, key=lambda i: mesh.area_faces[i])
            normal = mesh.face_normals[largest_face]
            axis = np.cross(normal, z_down)
            angle = np.arccos(np.clip(np.dot(normal, z_down), -1.0, 1.0))
            if np.linalg.norm(axis) > 1e-6 and angle > 1e-4:
                mesh.apply_transform(rotation_matrix(angle, axis))

        # --- Step 4: Align front face to +Y ---
        y_axis = np.array([0, 1, 0])
        front_faces = [(i, np.dot(face_normals[i], y_axis)) for i in range(len(face_normals))]
        best_face_index, _ = max(front_faces, key=lambda item: item[1])
        front_normal = face_normals[best_face_index]
        rotation_mat = align_vectors(front_normal, y_axis)
        mesh.apply_transform(rotation_mat)

        # --- Step 5: Ensure positive axis directions ---
        bounds = mesh.bounds
        if bounds[1][0] < bounds[0][0]:
            mesh.apply_transform(trimesh.transformations.scale_matrix(-1, [1, 0, 0]))
        if bounds[1][1] < bounds[0][1]:
            mesh.apply_transform(trimesh.transformations.scale_matrix(-1, [0, 1, 0]))
        if bounds[1][2] < bounds[0][2]:
            mesh.apply_transform(trimesh.transformations.scale_matrix(-1, [0, 0, 1]))

        # --- Step 6: Dimensions after full alignment ---
        dims_after = calculate_dimensions(mesh)

        # --- Step 7: Calculate volume in cubic centimeters (cc) ---
      
        # Clean up temp file
        os.remove(tmp_path)

        results.append({
            "filename": file.filename,
            "dimensions_before": {k: round(v, 2) for k, v in dims_before.items()},
            "dimensions_after": {k: round(v, 2) for k, v in dims_after.items()},
            
        })

    return results


if __name__ == "__main__":
    import uvicorn


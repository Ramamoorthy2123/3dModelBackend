from fastapi import FastAPI, File, UploadFile, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from stl import mesh
import tempfile
import os
import asyncio

router = APIRouter()

async def process_single_stl(file: UploadFile):
    """Process individual STL file and return its volume"""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".stl") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        your_mesh = mesh.Mesh.from_file(tmp_path)
        volume, _, _ = your_mesh.get_mass_properties()
        return {
            "filename": file.filename,
            "volume_cc": round(volume/1000 )  # Convert mm³ to cm³
        }
    except Exception as e:
        return {
            "filename": file.filename,
            "error": str(e)
        }
    finally:
        os.remove(tmp_path)

@router.post("/upload-multiple-stl/")
async def upload_multiple_stl(files: list[UploadFile] = File(...)):
    """
    Process multiple STL files concurrently.
    Returns individual results for each file.
    """
    # Create tasks for all files
    tasks = [process_single_stl(file) for file in files]
    
    # Run processing concurrently
    results = await asyncio.gather(*tasks)
    
    # Separate successful results and errors
    response = {
        "processed": [],
        "errors": []
    }
    
    for result in results:
        if "error" in result:
            response["errors"].append(result)
        else:
            response["processed"].append(result)
    
    return JSONResponse(content=response)

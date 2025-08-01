from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, Union, List
import threading
import logging
import os
import signal
import subprocess
import json
from datetime import datetime
import glob
from pathlib import Path

from src.workflow_executor import WorkflowExecutor
from src.node_composer import (
    run_discovery,
    generate_json_spec,
    get_node_registry,
    get_node_class_registry
)

router = APIRouter(
    prefix="/node",
    tags=["node"],
    responses={404: {"description": "Not found"}},
)

class Workflow(BaseModel):
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    view: Dict[str, Any]

def get_node_list():
    try:
        exported_nodes_path = "./constants/exported_nodes.json"
        if not os.path.exists(exported_nodes_path):
            raise HTTPException(status_code=404, detail="No nodes available. Please run discovery first.")
        with open(exported_nodes_path, 'r') as file:
            nodes = json.load(file)

        if not nodes:
            raise HTTPException(status_code=404, detail="No nodes available. Please run discovery first.")
        
        return nodes

    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal Server Error")

@router.get("/get", response_model=List[Dict[str, Any]])
async def list_nodes():
    try:
        nodes = get_node_list()
        return nodes
    except HTTPException as e:
        raise e
    except Exception as e:
        logging.error(f"Error listing nodes: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@router.get("/export", response_model=Dict[str, Any])
async def export_nodes():
    """
    Refesh and export the list of nodes to a JSON file.
    """
    try:
        from src.node_composer import NODE_REGISTRY
        run_discovery()
        output_filename = "./constants/exported_nodes.json"
        generate_json_spec(output_path=output_filename)
        if not os.path.exists(output_filename):
            raise HTTPException(status_code=500, detail="Failed to generate nodes JSON file.")
        
        else:
            return {"status": "success", "message": "Nodes exported successfully", "file": output_filename}
        
    except Exception as e:
        logging.error(f"Error listing nodes: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@router.post("/execute", response_model=Dict[str, Any])
async def execute_workflow(workflow: Workflow):
    """
    주어진 노드와 엣지 정보로 워크플로우를 실행합니다.
    """
    
    # print("DEBUG: 워크플로우 실행 요청\n", workflow)
    
    try:
        workflow_data = workflow.dict()
        executor = WorkflowExecutor(workflow_data)
        final_outputs = executor.execute_workflow()
        
        return {"status": "success", "message": "워크플로우 실행 완료", "outputs": final_outputs}

    except ValueError as e:
        logging.error(f"Workflow execution error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@router.get("/registry", response_model=Dict[str, Any])
async def get_node_registry_info():
    """
    전역 NODE_REGISTRY에서 노드 정보를 가져옵니다.
    """
    try:
        node_registry = get_node_registry()
        node_class_registry = get_node_class_registry()
        
        return {
            "status": "success",
            "node_count": len(node_registry),
            "available_nodes": [node["id"] for node in node_registry],
            "registry_data": node_registry,
            "class_registry_keys": list(node_class_registry.keys())
        }
        
    except Exception as e:
        logging.error(f"Error getting node registry: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@router.get("/registry/nodes", response_model=List[Dict[str, Any]])
async def get_all_nodes():
    """
    등록된 모든 노드의 정보를 반환합니다.
    """
    try:
        node_registry = get_node_registry()
        return node_registry
        
    except Exception as e:
        logging.error(f"Error getting all nodes: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@router.get("/registry/node/{node_id}", response_model=Dict[str, Any])
async def get_node_by_id(node_id: str):
    """
    특정 ID의 노드 정보를 반환합니다.
    """
    try:
        node_registry = get_node_registry()
        for node in node_registry:
            if node["id"] == node_id:
                return node
        
        raise HTTPException(status_code=404, detail=f"Node with id '{node_id}' not found")
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error getting node by id: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@router.get("/search", response_model=List[Dict[str, Any]])
async def search_nodes(
    query: str = None,
    tags: str = None,
    category: str = None,
    function: str = None
):
    """
    description, tags, category, function 등으로 노드를 검색합니다.
    """
    try:
        node_registry = get_node_registry()
        filtered_nodes = node_registry.copy()
        
        # query로 description과 nodeName 검색
        if query:
            query_lower = query.lower()
            filtered_nodes = [
                node for node in filtered_nodes
                if query_lower in node.get("description", "").lower() or 
                   query_lower in node.get("nodeName", "").lower()
            ]
        
        # tags로 필터링 (쉼표로 구분된 태그들)
        if tags:
            search_tags = [tag.strip().lower() for tag in tags.split(",")]
            filtered_nodes = [
                node for node in filtered_nodes
                if any(
                    search_tag in [node_tag.lower() for node_tag in node.get("tags", [])]
                    for search_tag in search_tags
                )
            ]
        
        # category로 필터링
        if category:
            filtered_nodes = [
                node for node in filtered_nodes
                if node.get("categoryId", "").lower() == category.lower()
            ]
        
        # function으로 필터링
        if function:
            filtered_nodes = [
                node for node in filtered_nodes
                if node.get("functionId", "").lower() == function.lower()
            ]
        
        return filtered_nodes
        
    except Exception as e:
        logging.error(f"Error searching nodes: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@router.get("/tags", response_model=List[str])
async def get_all_tags():
    """
    등록된 모든 노드의 태그들을 중복 제거하여 반환합니다.
    """
    try:
        node_registry = get_node_registry()
        all_tags = set()
        
        for node in node_registry:
            for tag in node.get("tags", []):
                all_tags.add(tag)
        
        return sorted(list(all_tags))
        
    except Exception as e:
        logging.error(f"Error getting all tags: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@router.get("/categories", response_model=List[Dict[str, str]])
async def get_all_categories():
    """
    등록된 모든 노드의 카테고리들을 반환합니다.
    """
    try:
        node_registry = get_node_registry()
        categories = {}
        
        for node in node_registry:
            cat_id = node.get("categoryId")
            cat_name = node.get("categoryName")
            if cat_id and cat_name:
                categories[cat_id] = cat_name
        
        return [{"id": cat_id, "name": cat_name} for cat_id, cat_name in categories.items()]
        
    except Exception as e:
        logging.error(f"Error getting all categories: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@router.get("/parameters/categorized/{node_id}", response_model=Dict[str, Any])
async def get_categorized_parameters(node_id: str):
    """
    특정 노드의 파라미터를 기본/고급으로 분류하여 반환합니다.
    """
    try:
        node_registry = get_node_registry()
        for node in node_registry:
            if node["id"] == node_id:
                parameters = node.get("parameters", [])
                
                basic_params = []
                advanced_params = []
                
                for param in parameters:
                    if param.get("optional", False):
                        advanced_params.append(param)
                    else:
                        basic_params.append(param)
                
                return {
                    "node_id": node_id,
                    "node_name": node.get("nodeName", ""),
                    "description": node.get("description", ""),
                    "basic_parameters": basic_params,
                    "advanced_parameters": advanced_params,
                    "has_advanced": len(advanced_params) > 0
                }
        
        raise HTTPException(status_code=404, detail=f"Node with id '{node_id}' not found")
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error getting categorized parameters: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@router.get("/parameters/validation", response_model=Dict[str, Any])
async def validate_all_parameters():
    """
    등록된 모든 노드의 파라미터 유효성을 검사합니다.
    """
    try:
        from src.model.node import validate_parameters
        
        node_registry = get_node_registry()
        validation_results = []
        
        for node in node_registry:
            parameters = node.get("parameters", [])
            if parameters:
                is_valid, errors = validate_parameters(parameters)
                validation_results.append({
                    "node_id": node["id"],
                    "node_name": node.get("nodeName", ""),
                    "is_valid": is_valid,
                    "errors": errors,
                    "parameter_count": len(parameters)
                })
        
        valid_count = sum(1 for result in validation_results if result["is_valid"])
        
        return {
            "total_nodes": len(validation_results),
            "valid_nodes": valid_count,
            "invalid_nodes": len(validation_results) - valid_count,
            "validation_results": validation_results
        }
        
    except Exception as e:
        logging.error(f"Error validating parameters: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
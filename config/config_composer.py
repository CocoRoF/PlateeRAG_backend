"""
Config Composer - 모든 설정을 통합 관리 (자동 발견 기능 포함)
"""
import os
import importlib
import logging
from typing import Dict, Any, Type
from pathlib import Path
from config.base_config import BaseConfig
from config.persistent_config import PersistentConfig, export_config_summary, refresh_all_configs, save_all_configs
from config.database_manager import initialize_database

logger = logging.getLogger("config-composer")

class ConfigComposer:
    """
    모든 설정을 자동 발견하여 통합적으로 관리하는 클래스
    sub_config/ 디렉토리의 *_config.py 파일들을 자동으로 스캔하고 로드합니다.
    """
    
    def __init__(self):
        # 동적으로 로드된 설정 카테고리들을 저장
        self.config_categories: Dict[str, Any] = {}
        
        # 모든 설정을 저장하는 딕셔너리
        self.all_configs: Dict[str, PersistentConfig] = {}
        
        self.logger = logger
        
        # 설정 카테고리들을 자동으로 발견하고 로드
        self._discover_and_load_configs()
    
    def _discover_and_load_configs(self):
        """
        sub_config/ 디렉토리에서 *_config.py 파일들을 자동으로 발견하고 로드
        """
        sub_config_dir = Path(__file__).parent / "sub_config"
        
        # *_config.py 패턴의 파일들 찾기
        config_files = []
        for file in sub_config_dir.glob("*_config.py"):
            if file.name != "__init__.py":
                config_files.append(file)
        
        self.logger.info(f"Found {len(config_files)} config files: {[f.name for f in config_files]}")
        
        # 각 설정 파일을 동적으로 로드
        for config_file in config_files:
            try:
                # 파일명에서 카테고리명 추출 (예: openai_config.py -> openai)
                category_name = config_file.stem.replace("_config", "")
                
                # 모듈 이름 생성
                module_name = f"config.sub_config.{config_file.stem}"
                
                # 동적으로 모듈 import
                module = importlib.import_module(module_name)
                
                # 클래스 이름을 다양한 방식으로 시도
                possible_class_names = [
                    f"{category_name.title()}Config",           # OpenaiConfig
                    f"{category_name.upper()}Config",           # OPENAIConfig
                    f"{category_name.capitalize()}Config",      # OpenaiConfig
                    "".join(word.capitalize() for word in category_name.split("_")) + "Config"  # OpenAIConfig (if underscore separated)
                ]
                
                config_class = None
                for class_name in possible_class_names:
                    if hasattr(module, class_name):
                        config_class = getattr(module, class_name)
                        break
                
                if config_class is None:
                    # 마지막 시도: 모듈에서 BaseConfig를 상속받은 클래스 찾기
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if (isinstance(attr, type) and 
                            issubclass(attr, BaseConfig) and 
                            attr != BaseConfig):
                            config_class = attr
                            break
                
                if config_class is None:
                    raise AttributeError(f"No valid config class found in {module_name}")
                
                # 인스턴스 생성
                config_instance = config_class()
                
                # 카테고리로 저장
                self.config_categories[category_name] = config_instance
                
                # 동적 속성으로 설정 (self.openai, self.app 등)
                setattr(self, category_name, config_instance)
                
                self.logger.info("Successfully loaded config category: %s", category_name)
                
            except ImportError as e:
                self.logger.error("Failed to load config file %s: %s", config_file.name, e)
        
        self.logger.info("Auto-discovered %d config categories: %s", len(self.config_categories), list(self.config_categories.keys()))
    
    def initialize_all_configs(self) -> Dict[str, Any]:
        """
        모든 설정을 초기화하고 app.state에 저장할 데이터 구조 반환
        """
        try:
            self.logger.info("Initializing all configurations...")
            
            # 1. 데이터베이스 설정 우선 초기화
            if "database" in self.config_categories:
                database_configs = self.config_categories["database"].initialize()
                self.all_configs.update(database_configs)
                
                # 2. 데이터베이스 초기화 (마이그레이션 포함)
                db_initialized = initialize_database(self.config_categories["database"])
                if db_initialized:
                    self.logger.info("Database initialized successfully")
                else:
                    self.logger.warning("Database initialization failed, using JSON fallback")
            
            # 3. 나머지 설정 카테고리들 초기화
            for category_name, config_instance in self.config_categories.items():
                if category_name != "database":  # 데이터베이스는 이미 초기화됨
                    try:
                        category_configs = config_instance.initialize()
                        self.all_configs.update(category_configs)
                        self.logger.info("Initialized %s config: %d settings", category_name, len(category_configs))
                    except AttributeError as e:
                        self.logger.error("Failed to initialize %s config: %s", category_name, e)
            
            self.logger.info("Successfully initialized %d configurations", len(self.all_configs))
            
            # app.state에 저장할 구조화된 데이터 반환
            result = self.config_categories.copy()
            result["all_configs"] = self.all_configs
            return result
            
        except Exception as e:
            self.logger.error("Failed to initialize configurations: %s", e)
            raise
    
    def get_config_by_name(self, config_name: str) -> PersistentConfig:
        """
        이름으로 특정 설정 가져오기
        """
        if config_name in self.all_configs:
            return self.all_configs[config_name]
        raise KeyError(f"Configuration '{config_name}' not found")
    
    def get_config_summary(self) -> Dict[str, Any]:
        """
        모든 설정의 요약 정보 반환 (동적 카테고리 지원)
        """
        categories_summary = {}
        for category_name, config_instance in self.config_categories.items():
            try:
                categories_summary[category_name] = config_instance.get_config_summary()
            except AttributeError as e:
                self.logger.error("Failed to get summary for %s: %s", category_name, e)
                categories_summary[category_name] = {"error": str(e)}
        
        return {
            "total_configs": len(self.all_configs),
            "discovered_categories": list(self.config_categories.keys()),
            "categories": categories_summary,
            "persistent_summary": export_config_summary()
        }
    
    def refresh_all(self):
        """
        모든 설정을 데이터베이스에서 다시 로드
        """
        self.logger.info("Refreshing all configurations from database...")
        refresh_all_configs()
        self.logger.info("All configurations refreshed successfully")
    
    def save_all(self):
        """
        모든 설정을 데이터베이스에 저장
        """
        self.logger.info("Saving all configurations to database...")
        save_all_configs()
        self.logger.info("All configurations saved successfully")
    
    def ensure_directories(self):
        """
        필요한 디렉토리들 생성 (동적 카테고리 지원)
        """
        # app 카테고리에서 DATA_DIRECTORIES 찾기
        if "app" in self.config_categories:
            app_config = self.config_categories["app"]
            if hasattr(app_config, "DATA_DIRECTORIES"):
                directories = app_config.DATA_DIRECTORIES.value
                
                for directory in directories:
                    if not os.path.exists(directory):
                        os.makedirs(directory, exist_ok=True)
                        self.logger.info("Created directory: %s", directory)
                    else:
                        self.logger.info("Directory already exists: %s", directory)
            else:
                self.logger.warning("No DATA_DIRECTORIES found in app config")
        else:
            self.logger.warning("No app config category found")
    
    def validate_critical_configs(self) -> Dict[str, Any]:
        """
        중요한 설정들이 올바르게 설정되었는지 검증 (동적 카테고리 지원)
        """
        validation_results = {
            "valid": True,
            "warnings": [],
            "errors": []
        }
        
        # OpenAI API 키 검증 (openai 카테고리가 있는 경우)
        if "openai" in self.config_categories:
            openai_config = self.config_categories["openai"]
            if hasattr(openai_config, "API_KEY"):
                api_key = openai_config.API_KEY.value
                if not api_key or not api_key.strip():
                    validation_results["warnings"].append("OpenAI API key is not configured")
        
        # 포트 범위 검증 (app 카테고리가 있는 경우)
        if "app" in self.config_categories:
            app_config = self.config_categories["app"]
            if hasattr(app_config, "PORT"):
                port = app_config.PORT.value
                if not (1 <= port <= 65535):
                    validation_results["errors"].append(f"Invalid port number: {port}")
                    validation_results["valid"] = False
        
        # 타임아웃 값 검증 (workflow 카테고리가 있는 경우)
        if "workflow" in self.config_categories:
            workflow_config = self.config_categories["workflow"]
            if hasattr(workflow_config, "EXECUTION_TIMEOUT"):
                workflow_timeout = workflow_config.EXECUTION_TIMEOUT.value
                if workflow_timeout <= 0:
                    validation_results["errors"].append(f"Invalid workflow timeout: {workflow_timeout}")
                    validation_results["valid"] = False
        
        return validation_results

# 전역 설정 컴포저 인스턴스
config_composer = ConfigComposer()

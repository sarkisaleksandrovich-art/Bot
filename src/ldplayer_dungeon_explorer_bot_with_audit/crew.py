import os


from crewai import LLM
from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import (
	ExaSearchTool,
	ScrapeWebsiteTool
)






@CrewBase
class LdplayerDungeonExplorerBotWithAuditCrew:
    """LdplayerDungeonExplorerBotWithAudit crew"""

    
    @agent
    def ldplayer_automation_researcher(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["ldplayer_automation_researcher"],
            
            
            tools=[				ExaSearchTool(),
				ScrapeWebsiteTool()],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.6-luna",
                
                
            ),
            
        )
        
    
    @agent
    def python_bot_developer(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["python_bot_developer"],
            
            
            tools=[],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.4",
                
                
            ),
            
        )
        
    
    @agent
    def bot_architect(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["bot_architect"],
            
            
            tools=[],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.4",
                
                
            ),
            
        )
        
    
    @agent
    def core_infrastructure_developer(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["core_infrastructure_developer"],
            
            
            tools=[],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.4",
                
                
            ),
            
        )
        
    
    @agent
    def computer_vision_developer(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["computer_vision_developer"],
            
            
            tools=[],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.4",
                
                
            ),
            
        )
        
    
    @agent
    def safety_and_popups_developer(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["safety_and_popups_developer"],
            
            
            tools=[],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.4",
                
                
            ),
            
        )
        
    
    @agent
    def cli_and_setup_tools_developer(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["cli_and_setup_tools_developer"],
            
            
            tools=[],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.4",
                
                
            ),
            
        )
        
    
    @agent
    def knowledge_base_developer(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["knowledge_base_developer"],
            
            
            tools=[],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.4",
                
                
            ),
            
        )
        
    
    @agent
    def explorer_logic_developer(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["explorer_logic_developer"],
            
            
            tools=[],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.4",
                
                
            ),
            
        )
        
    
    @agent
    def location_profiles_developer(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["location_profiles_developer"],
            
            
            tools=[],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.4",
                
                
            ),
            
        )
        
    
    @agent
    def floor_exploration_developer(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["floor_exploration_developer"],
            
            
            tools=[],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.4",
                
                
            ),
            
        )
        
    
    @agent
    def adb_module_developer(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["adb_module_developer"],
            
            
            tools=[],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.4",
                
                
            ),
            
        )
        
    
    @agent
    def teacher_ui_developer(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["teacher_ui_developer"],
            
            
            tools=[],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.4",
                
                
            ),
            
        )
        
    
    @agent
    def screenshot_module_developer(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["screenshot_module_developer"],
            
            
            tools=[],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.4",
                
                
            ),
            
        )
        
    
    @agent
    def config_and_requirements_developer(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["config_and_requirements_developer"],
            
            
            tools=[],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.4",
                
                
            ),
            
        )
        
    
    @agent
    def floor_state_developer(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["floor_state_developer"],
            
            
            tools=[],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.4",
                
                
            ),
            
        )
        
    
    @agent
    def cross_module_static_auditor(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["cross_module_static_auditor"],
            
            
            tools=[],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.4",
                
                
            ),
            
        )
        
    

    
    @task
    def research_ldplayer_bot_tech_stack(self) -> Task:
        return Task(
            config=self.tasks_config["research_ldplayer_bot_tech_stack"],
            markdown=False,
            
            
        )
    
    @task
    def design_architecture_and_contracts(self) -> Task:
        return Task(
            config=self.tasks_config["design_architecture_and_contracts"],
            markdown=False,
            
            
        )
    
    @task
    def write_core_modules(self) -> Task:
        return Task(
            config=self.tasks_config["write_core_modules"],
            markdown=False,
            
            
        )
    
    @task
    def write_vision_module(self) -> Task:
        return Task(
            config=self.tasks_config["write_vision_module"],
            markdown=False,
            
            
        )
    
    @task
    def write_safety_module(self) -> Task:
        return Task(
            config=self.tasks_config["write_safety_module"],
            markdown=False,
            
            
        )
    
    @task
    def write_knowledge_base(self) -> Task:
        return Task(
            config=self.tasks_config["write_knowledge_base"],
            markdown=False,
            
            
        )
    
    @task
    def write_adb_module(self) -> Task:
        return Task(
            config=self.tasks_config["write_adb_module"],
            markdown=False,
            
            
        )
    
    @task
    def write_location_profiles_module(self) -> Task:
        return Task(
            config=self.tasks_config["write_location_profiles_module"],
            markdown=False,
            
            
        )
    
    @task
    def write_teacher_ui(self) -> Task:
        return Task(
            config=self.tasks_config["write_teacher_ui"],
            markdown=False,
            
            
        )
    
    @task
    def write_screenshot_module(self) -> Task:
        return Task(
            config=self.tasks_config["write_screenshot_module"],
            markdown=False,
            
            
        )
    
    @task
    def write_config_yaml_and_requirements(self) -> Task:
        return Task(
            config=self.tasks_config["write_config_yaml_and_requirements"],
            markdown=False,
            
            
        )
    
    @task
    def write_floor_state_module(self) -> Task:
        return Task(
            config=self.tasks_config["write_floor_state_module"],
            markdown=False,
            
            
        )
    
    @task
    def write_floor_exploration_module(self) -> Task:
        return Task(
            config=self.tasks_config["write_floor_exploration_module"],
            markdown=False,
            
            
        )
    
    @task
    def write_explorer_loop(self) -> Task:
        return Task(
            config=self.tasks_config["write_explorer_loop"],
            markdown=False,
            
            
        )
    
    @task
    def write_cli_entry_point(self) -> Task:
        return Task(
            config=self.tasks_config["write_cli_entry_point"],
            markdown=False,
            
            
        )
    
    @task
    def final_cross_module_audit(self) -> Task:
        return Task(
            config=self.tasks_config["final_cross_module_audit"],
            markdown=False,
            
            
        )
    
    @task
    def assemble_final_package(self) -> Task:
        return Task(
            config=self.tasks_config["assemble_final_package"],
            markdown=False,
            
            
        )
    

    @crew
    def crew(self) -> Crew:
        """Creates the LdplayerDungeonExplorerBotWithAudit crew"""

        return Crew(
            agents=self.agents,  # Automatically created by the @agent decorator
            tasks=self.tasks,  # Automatically created by the @task decorator
            process=Process.sequential,
            verbose=True,

            chat_llm=LLM(model="openai/gpt-5.6-luna"),
        )



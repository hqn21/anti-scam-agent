from agents import Agent, Runner
from dotenv import load_dotenv
load_dotenv()

if __name__ == "__main__":
    agent = Agent(name="Assistant", instructions="You are a helpful assistant", model="gpt-4.1-mini")

    result = Runner.run_sync(agent, "Write a haiku about recursion in programming.")
    print(result.final_output)
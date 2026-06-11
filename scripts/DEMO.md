
```bash
cd $DIR

uv run https://kagenti-teleport-setup-team1.apps.epoc002.ete14.res.ibm.com/kagenti-teleport-setup.py  --user alice --password alice123 --test

alias kosh="uv run $PWD/kosh.py"
# optional setup CLI completion
./setup-kosh-completions.sh
exec zsh

kosh local-sandbox list

kosh sandbox list

# export CLAUDE_AUTH_TOKEN=...
# export CLAUDE_CODE_DISABLE_MOUSE=1
# export CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1
# export ANTHROPIC_BASE_URL="https://ete-litellm.ai-models.vpc-int.res.ibm.com"
# export ANTHROPIC_MODEL=claude-opus-4-6


kosh local-sandbox create --name ross1 --model claude-opus-4-6

# pwd
# ls /Users/aslom
# claude
# exit

kosh local-sandbox connect --name ross1

# claude -r

kosh sandbox list

kosh teleport

kosh sandbox list

kosh sandbox connect ross1

# id
# env|grep ANTH

# claude -r

# bob --accept-license --auth-method api-key -p "say hi"

# env|grep BOB


claude -p "say hi"

# === use kwiki skills

git clone https://github.com/kagenti/agent-examples.git

mkdir -p .claude/skills/
# install skills from https://github.com/kagenti/agent-examples/tree/main/mcp/wiki_memory_tool/skills
cp -rp agent-examples/mcp/wiki_memory_tool/skills/* .claude/skills/
ls .claude/skills/

claude -r

# run /kwiki cli query skill for Kagenti form wiki running at https://wiki-memory-service-team1.apps.ykt1.hcp.res.ibm.com/

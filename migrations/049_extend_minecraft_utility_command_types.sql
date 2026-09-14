DO $$
BEGIN
    ALTER TABLE minecraft_command_queue
        DROP CONSTRAINT IF EXISTS minecraft_command_queue_type_safe;

    ALTER TABLE minecraft_command_queue
        ADD CONSTRAINT minecraft_command_queue_type_safe
        CHECK (
            command_type IN (
                'narita_carpet',
                'structure_block',
                'command_block',
                'barrier_block',
                'light_block',
                'jigsaw_block',
                'structure_void',
                'repeating_command_block',
                'chain_command_block',
                'taketumi_spawn_egg',
                'held_item_inspect',
                'server_status'
            )
        );
END
$$;

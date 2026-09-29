// Sample TS file for outline tests.
// Header block: the outline prints these lines.

interface Config {
    name: string;
    value: number;
}

type Result = string | number;

/*
 * A three-line comment block.
 */
enum Status {
    Active,
    Inactive,
}

/// The service that runs the args.
class Service {
    run(args: string[]) {
        return args;
    }
}

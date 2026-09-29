// Sample C# file for outline tests.
// Header block: the outline prints these lines.
using System;

namespace ProjectArchitect.Core
{
    public class Engine
    {
        // Start the engine in the given mode.
        public void Start(string mode)
        {
            Console.WriteLine(mode);
        }

        // A three-line comment block:
        // the outline prints its range
        // and its first line.

        private int Calculate(int a, int b)
        {
            return a + b;
        }
    }

    public interface IPlugin
    {
        void Initialize();
    }
}
